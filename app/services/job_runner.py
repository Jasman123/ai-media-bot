"""Runs jobs against providers: submit with fallback, poll, apply results, deliver, refund (A1 error taxonomy, F11).

All state changes go through `JobRepository.transition` (conditional updates), so this code is safe to run
concurrently and to retry.
"""

import logging
import mimetypes
from datetime import timedelta
from decimal import Decimal

from ..domain.catalog import CatalogModel, PricingPolicy, margin_ok
from ..domain.generation import (
    GenerationRequest,
    InvalidInput,
    ModerationBlocked,
    ProviderDown,
    ProviderError,
    ProviderState,
    ProviderStatus,
    QuotaExhausted,
    Retryable,
)
from ..domain.jobs import (
    FAILABLE_STATUSES,
    ChannelUnreachable,
    DeliveryChannel,
    FailureReason,
    Job,
    JobPolicy,
    JobRepository,
    JobStatus,
    MediaFile,
    Progress,
)
from ..domain.ports import Clock, Storage, TaskQueue, UnitOfWork
from ..domain.tasks import POLL_JOB, QUEUE_JOBS, RUN_JOB
from .catalog import CatalogService
from .ledger import LedgerService
from .providers import ProviderRegistry

logger = logging.getLogger(__name__)
INPUT_URL_TTL_S = 3600


def reason_for(error: ProviderError | None) -> FailureReason:
    if isinstance(error, ModerationBlocked):
        return FailureReason.MODERATION
    if isinstance(error, InvalidInput):
        return FailureReason.INVALID_INPUT
    if isinstance(error, ProviderDown):
        return FailureReason.PROVIDER_DOWN
    if isinstance(error, Retryable):
        return FailureReason.PROVIDER_BUSY
    return FailureReason.PROVIDER_ERROR


class JobRunner:
    def __init__(self, jobs: JobRepository, catalog: CatalogService, ledger: LedgerService,
                 registry: ProviderRegistry, queue: TaskQueue, storage: Storage, channel: DeliveryChannel,
                 uow: UnitOfWork, clock: Clock, policy: JobPolicy, pricing: PricingPolicy):
        self._jobs = jobs
        self._catalog = catalog
        self._ledger = ledger
        self._registry = registry
        self._queue = queue
        self._storage = storage
        self._channel = channel
        self._uow = uow
        self._clock = clock
        self._policy = policy
        self._pricing = pricing

    # ------------------------------------------------------------------ submit
    async def run(self, job_id: str) -> None:
        now = self._clock.now()
        job = await self._jobs.transition(job_id, (JobStatus.QUEUED,), JobStatus.RUNNING,
                                          started_at=now, heartbeat_at=now, attempts_increment=1)
        if job is None:
            return  # already taken by another worker, or finished
        await self._uow.commit()

        chain = await self._catalog.chain(job.model_id, job.operation)
        if not chain:
            await self.fail(job, FailureReason.NO_MODEL)
            return

        last_reason = FailureReason.PROVIDER_DOWN
        for model in chain:
            req = await self._request(job, model)
            if model.id != job.model_id and not margin_ok(self._pricing, job.credits_charged,
                                                          self._catalog.cost(model, req)):
                logger.warning("Fallback %s skipped: below margin floor", model.id, extra={"job_id": job.id})
                continue
            provider = self._registry.get(model.provider)
            try:
                submitted = await provider.submit(req)
            except Retryable as exc:
                if job.attempts < self._policy.max_submit_retries:
                    await self._requeue(job, exc.retry_after_s)
                    return
                last_reason = FailureReason.PROVIDER_BUSY
                continue
            except QuotaExhausted:
                logger.error("Quota exhausted on %s; pausing model", model.id, extra={"provider": model.provider})
                await self._catalog.pause(model.id)
                continue
            except ProviderDown:
                logger.warning("Provider down for %s", model.id, extra={"provider": model.provider})
                continue
            except (ModerationBlocked, InvalidInput) as exc:
                await self.fail(job, reason_for(exc))
                return
            except ProviderError:
                logger.exception("Provider error on submit", extra={"job_id": job.id})
                last_reason = FailureReason.PROVIDER_ERROR
                continue

            job = await self._jobs.update(
                job.id, provider=model.provider, provider_job_id=submitted.provider_job_id, model_id=model.id,
                cost_usd_estimate=self._catalog.cost(model, req), heartbeat_at=self._clock.now(),
            )
            await self._uow.commit()
            if submitted.status.terminal:
                await self.apply_status(job.id, submitted.status)
            else:
                await self._schedule_poll(job)
            return
        await self.fail(job, last_reason)

    async def _request(self, job: Job, model: CatalogModel) -> GenerationRequest:
        p = job.params
        urls = tuple([await self._storage.url(key, INPUT_URL_TTL_S) for key in job.input_keys])
        webhook = (f"{self._policy.webhook_base_url.rstrip('/')}/webhook/provider/{model.provider}"
                   if self._policy.webhook_base_url else None)
        return GenerationRequest(
            operation=job.operation, provider_model_id=model.provider_model_id, prompt=job.prompt,
            negative_prompt=p.get("negative_prompt"), input_urls=urls, aspect_ratio=p.get("aspect_ratio", "1:1"),
            duration_s=p.get("duration_s"), resolution=p.get("resolution"), audio=bool(p.get("audio")),
            n=int(p.get("n", 1)), seed=p.get("seed"), webhook_url=webhook,
        )

    async def _requeue(self, job: Job, delay_s: float | None) -> None:
        requeued = await self._jobs.transition(job.id, (JobStatus.RUNNING,), JobStatus.QUEUED,
                                               heartbeat_at=self._clock.now())
        await self._uow.commit()
        if requeued:
            await self._queue.enqueue(RUN_JOB, job.id, queue=QUEUE_JOBS, job_id=f"run:{job.id}:{job.attempts}",
                                      defer_s=max(delay_s or 0, 1.0 * job.attempts))

    async def _schedule_poll(self, job: Job) -> None:
        await self._queue.enqueue(POLL_JOB, job.id, queue=QUEUE_JOBS, defer_s=self._policy.poll_s(job.kind))

    # ------------------------------------------------------------------ poll
    async def poll(self, job_id: str) -> None:
        job = await self._jobs.get(job_id)
        if job is None or job.status != JobStatus.RUNNING or not job.provider_job_id:
            return
        if job.deadline_at and self._clock.now() >= job.deadline_at:
            await self.timeout(job)
            return
        job = await self._jobs.update(job.id, heartbeat_at=self._clock.now())
        await self._uow.commit()
        provider = self._registry.get(job.provider)
        try:
            status = await provider.status(job.provider_job_id)
        except Retryable:
            await self._schedule_poll(job)
            return
        except ProviderError as exc:
            status = ProviderStatus(ProviderState.FAILED, error=exc)
        if status.terminal:
            await self.apply_status(job.id, status)
            return
        await self._notify_progress(job, status)
        await self._schedule_poll(job)

    async def _notify_progress(self, job: Job, status: ProviderStatus) -> None:
        try:
            eta = None
            if job.started_at and job.deadline_at:
                model = await self._catalog_model(job.model_id)
                if model:
                    elapsed = (self._clock.now() - job.started_at).total_seconds()
                    eta = max(5, int(model.eta_s - elapsed))
            await self._channel.progress(job, Progress(state=status.state.value,
                                                       queue_position=status.queue_position, eta_s=eta))
        except ChannelUnreachable:
            pass
        except Exception:
            logger.warning("Progress update failed", extra={"job_id": job.id}, exc_info=True)

    async def _catalog_model(self, model_id: str) -> CatalogModel | None:
        for model in await self._catalog.all():
            if model.id == model_id:
                return model
        return None

    async def timeout(self, job: Job) -> None:
        if job.provider and job.provider_job_id:
            try:
                await self._registry.get(job.provider).cancel(job.provider_job_id)
            except Exception:
                logger.warning("Cancel failed", extra={"job_id": job.id}, exc_info=True)
        await self.fail(job, FailureReason.TIMEOUT)

    # ------------------------------------------------------------------ results
    async def apply_status(self, job_id: str, status: ProviderStatus) -> None:
        """Terminal provider status → deliver or refund. Idempotent: only one caller wins the claim."""
        if not status.terminal:
            return
        if status.state == ProviderState.FAILED:
            job = await self._jobs.get(job_id)
            if job is not None and job.status == JobStatus.RUNNING:
                await self.fail(job, reason_for(status.error))
            return

        job = await self._jobs.transition(job_id, (JobStatus.RUNNING,), JobStatus.DELIVERING,
                                          heartbeat_at=self._clock.now())
        if job is None:
            return
        await self._uow.commit()
        provider = self._registry.get(job.provider)
        try:
            files = []
            for index, url in enumerate(status.result.urls):
                content, mime = await provider.fetch(url, self._policy.max_result_bytes)
                mime = mime or status.result.mime
                ext = mimetypes.guess_extension(mime) or ".bin"
                key = f"results/{job.id}/{index}{ext}"
                await self._storage.put(key, content, mime)
                files.append(MediaFile(content=content, mime=mime, filename=f"{job.id[:8]}-{index}{ext}",
                                       storage_key=key))
        except ProviderError as exc:
            back = await self._jobs.transition(job.id, (JobStatus.DELIVERING,), JobStatus.RUNNING)
            await self._uow.commit()
            if back:
                await self.fail(back, reason_for(exc))
            return

        try:
            refs = await self._channel.deliver(job, files)
        except ChannelUnreachable:
            back = await self._jobs.transition(job.id, (JobStatus.DELIVERING,), JobStatus.RUNNING,
                                               result_keys=tuple(f.storage_key for f in files))
            await self._uow.commit()
            if back:
                await self.fail(back, FailureReason.DELIVERY, notify=False)
            return
        except Exception:
            # Transient (e.g. Telegram 5xx/429): put the job back so a retry or the sweeper delivers it.
            await self._jobs.transition(job.id, (JobStatus.DELIVERING,), JobStatus.RUNNING)
            await self._uow.commit()
            raise

        actual = status.result.cost_usd_actual
        await self._ledger.capture(job.reservation_id, commit=False)
        await self._jobs.transition(
            job.id, (JobStatus.DELIVERING,), JobStatus.DELIVERED, finished_at=self._clock.now(),
            result_keys=tuple(f.storage_key for f in files), result_refs=refs,
            cost_usd_actual=Decimal(str(actual)) if actual is not None else job.cost_usd_estimate,
        )
        await self._uow.commit()

    # ------------------------------------------------------------------ failure
    async def fail(self, job: Job, reason: FailureReason, notify: bool = True) -> None:
        """Release the reservation (auto-refund) and tell the user. Only the first caller acts."""
        refunded = job.reservation_id is not None and job.credits_charged > 0
        updated = await self._jobs.transition(
            job.id, FAILABLE_STATUSES, JobStatus.REFUNDED if refunded else JobStatus.FAILED,
            error_code=reason.value, finished_at=self._clock.now(),
        )
        if updated is None:
            return
        if refunded:
            await self._ledger.release(job.reservation_id, commit=False)
        await self._uow.commit()
        if notify:
            try:
                await self._channel.failed(updated, reason, refunded)
            except ChannelUnreachable:
                pass
            except Exception:
                logger.warning("Failure notice not sent", extra={"job_id": job.id}, exc_info=True)

    async def recover(self, job_id: str) -> None:
        """After an unexpected error in a task: make sure a running job keeps being polled."""
        await self._uow.rollback()
        job = await self._jobs.get(job_id)
        if job is not None and job.status == JobStatus.RUNNING and job.provider_job_id:
            await self._schedule_poll(job)

    # ------------------------------------------------------------------ sweeper
    async def sweep(self) -> dict[str, int]:
        """Cron safety net: requeue lost jobs, time out overdue ones, recover interrupted deliveries."""
        now = self._clock.now()
        counts = {"requeued": 0, "timed_out": 0, "recovered": 0, "polled": 0}
        for job in await self._jobs.overdue(now):
            await self.fail(job, FailureReason.TIMEOUT)
            counts["timed_out"] += 1
        older = now - timedelta(seconds=self._policy.stuck_after_s)
        for job in await self._jobs.stale(JobStatus.QUEUED, older):
            await self._jobs.update(job.id, heartbeat_at=now)
            await self._uow.commit()
            await self._queue.enqueue(RUN_JOB, job.id, queue=QUEUE_JOBS,
                                      job_id=f"run:{job.id}:sweep:{int(now.timestamp())}")
            counts["requeued"] += 1
        for job in await self._jobs.stale(JobStatus.DELIVERING, older):
            if await self._jobs.transition(job.id, (JobStatus.DELIVERING,), JobStatus.RUNNING, heartbeat_at=now):
                await self._uow.commit()
                await self._schedule_poll(job)
                counts["recovered"] += 1
        for job in await self._jobs.stale(JobStatus.RUNNING, older):
            # A healthy poll chain refreshes the heartbeat every few seconds; a stale one means it died.
            if job.provider_job_id:
                await self._jobs.update(job.id, heartbeat_at=now)
                await self._uow.commit()
                await self._schedule_poll(job)
                counts["polled"] += 1
            else:
                await self._requeue(job, None)
                counts["requeued"] += 1
        return counts
