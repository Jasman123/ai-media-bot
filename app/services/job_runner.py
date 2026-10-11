"""Runs jobs against providers and delivers results (A1 error taxonomy, F11 auto-refund).

See app/domain/jobs.py for the state machine and the lease rules this code relies on. Every write is a
compare-and-set, so the methods below are safe to run concurrently, to retry, and to resume after a crash.
"""

import logging
import mimetypes
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from ..domain.billing import ReservationConflict
from ..domain.catalog import CatalogModel, PricingPolicy, margin_ok
from ..domain.errors import NotFound
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
    DeliveryRejected,
    FailureReason,
    Job,
    JobPolicy,
    JobRepository,
    JobStatus,
    LeaseLost,
    MediaFile,
    Progress,
)
from ..domain.ports import Clock, Storage, TaskQueue, UnitOfWork
from ..domain.tasks import DELIVER_JOB, POLL_JOB, QUEUE_JOBS, RUN_JOB
from .catalog import CatalogService
from .ledger import LedgerService
from .providers import ProviderRegistry

logger = logging.getLogger(__name__)
INPUT_URL_TTL_S = 3600


def reason_for(error: BaseException | None) -> FailureReason:
    if isinstance(error, ModerationBlocked):
        return FailureReason.MODERATION
    if isinstance(error, InvalidInput):
        return FailureReason.INVALID_INPUT
    if isinstance(error, ProviderDown):
        return FailureReason.PROVIDER_DOWN
    if isinstance(error, Retryable):
        return FailureReason.PROVIDER_BUSY
    return FailureReason.PROVIDER_ERROR


class _Progress:
    """DeliveryProgress backed by the job row; every step write is fenced by the delivery lease owner."""

    def __init__(self, runner: "JobRunner", job: Job, owner: str):
        self._runner = runner
        self._job = job
        self._owner = owner
        self.steps: dict[str, Any] = job.delivery_steps

    @property
    def any_sent(self) -> bool:
        return bool(self.steps)

    def is_done(self, step: str) -> bool:
        return step in self.steps

    def refs(self) -> dict[str, dict[str, Any]]:
        return dict(self.steps)

    async def mark_done(self, step: str, refs: dict[str, Any]) -> None:
        self.steps[step] = refs
        await self._runner._fenced(self._job, self._owner, result_refs={**self._job.result_refs, "steps": self.steps})


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

    def _lease(self, seconds: float) -> datetime:
        return self._clock.now() + timedelta(seconds=seconds + self._policy.stuck_after_s)

    # ================================================================== submit
    async def run(self, job_id: str) -> None:
        now = self._clock.now()
        job = await self._jobs.transition(job_id, (JobStatus.QUEUED,), JobStatus.RUNNING, started_at=now,
                                          attempts_increment=1, lease_until=self._lease(self._policy.submit_lease_s),
                                          provider=None, provider_job_id=None)
        if job is None:
            return  # taken by another worker, or no longer queued
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
            # Renew the submit lease before each (possibly slow) call; stop if the sweeper took the job back.
            if not await self._renew_submit(job):
                return
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

            claimed = await self._jobs.transition(
                job.id, (JobStatus.RUNNING,), JobStatus.RUNNING, conditions={"attempts": job.attempts},
                provider=model.provider, provider_job_id=submitted.provider_job_id, model_id=model.id,
                cost_usd_estimate=self._catalog.cost(model, req), poll_seq=0,
                lease_until=self._lease(self._policy.poll_s(job.kind)),
            )
            await self._uow.commit()
            if claimed is None:
                # The sweeper requeued this job while we were submitting: don't leave an orphan generation.
                await self._cancel_quietly(model.provider, submitted.provider_job_id)
                return
            if submitted.status.terminal:
                await self.apply_status(job.id, submitted.status)
            else:
                await self._enqueue_poll(claimed)
            return
        await self.fail(job, last_reason)

    async def _renew_submit(self, job: Job) -> bool:
        renewed = await self._jobs.transition(job.id, (JobStatus.RUNNING,), JobStatus.RUNNING,
                                              conditions={"attempts": job.attempts},
                                              lease_until=self._lease(self._policy.submit_lease_s))
        await self._uow.commit()
        return renewed is not None

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

    async def _requeue(self, job: Job, delay_s: float | None) -> bool:
        delay = max(delay_s or 0.0, 1.0 * job.attempts)
        requeued = await self._jobs.transition(job.id, (JobStatus.RUNNING,), JobStatus.QUEUED,
                                               conditions={"attempts": job.attempts}, lease_until=self._lease(delay))
        await self._uow.commit()
        if requeued:
            await self._queue.enqueue(RUN_JOB, job.id, queue=QUEUE_JOBS, job_id=f"run:{job.id}:{job.attempts}",
                                      defer_s=delay)
        return requeued is not None

    async def _cancel_quietly(self, provider_name: str | None, provider_job_id: str | None) -> None:
        if not provider_name or not provider_job_id:
            return
        try:
            await self._registry.get(provider_name).cancel(provider_job_id)
        except Exception:
            logger.warning("Cancel failed for %s", provider_job_id, exc_info=True)

    # ================================================================== poll
    async def _enqueue_poll(self, job: Job) -> None:
        await self._queue.enqueue(POLL_JOB, job.id, job.poll_seq, queue=QUEUE_JOBS,
                                  job_id=f"poll:{job.id}:{job.poll_seq}", defer_s=self._policy.poll_s(job.kind))

    async def poll(self, job_id: str, seq: int = 0) -> None:
        # Compare-and-set on poll_seq: only the current chain continues; superseded chains stop here.
        # The lease uses the longer (video) interval: the kind isn't known before the row is read.
        job = await self._jobs.transition(job_id, (JobStatus.RUNNING,), JobStatus.RUNNING,
                                          conditions={"poll_seq": seq}, poll_seq=seq + 1,
                                          lease_until=self._lease(self._policy.video_poll_s))
        if job is None:
            return
        await self._uow.commit()
        if not job.provider_job_id:
            return
        if job.deadline_at and self._clock.now() >= job.deadline_at:
            await self.timeout(job)
            return
        try:
            status = await self._registry.get(job.provider).status(job.provider_job_id)
        except (ProviderError, NotFound):
            # Status API hiccup: the generation may still be running (and billing). Keep polling until the deadline.
            logger.warning("Status check failed; will retry", extra={"job_id": job.id}, exc_info=True)
            await self._enqueue_poll(job)
            return
        if status.terminal:
            await self.apply_status(job.id, status)
            return
        await self._notify_progress(job, status)
        await self._enqueue_poll(job)

    async def revive_polling(self, job: Job) -> bool:
        """Start a fresh poll chain; bumping poll_seq makes any older chain stop at its next step."""
        bumped = await self._jobs.transition(job.id, (JobStatus.RUNNING,), JobStatus.RUNNING,
                                             conditions={"poll_seq": job.poll_seq}, poll_seq=job.poll_seq + 1,
                                             lease_until=self._lease(self._policy.poll_s(job.kind)))
        await self._uow.commit()
        if bumped:
            await self._enqueue_poll(bumped)
        return bumped is not None

    async def _notify_progress(self, job: Job, status: ProviderStatus) -> None:
        try:
            eta = None
            model = next((m for m in await self._catalog.all() if m.id == job.model_id), None)
            if model and job.started_at:
                elapsed = (self._clock.now() - job.started_at).total_seconds()
                eta = max(5, int(model.eta_s - elapsed))
            await self._channel.progress(job, Progress(state=status.state.value,
                                                       queue_position=status.queue_position, eta_s=eta))
        except ChannelUnreachable:
            pass
        except Exception:
            logger.warning("Progress update failed", extra={"job_id": job.id}, exc_info=True)

    async def timeout(self, job: Job) -> None:
        await self._cancel_quietly(job.provider, job.provider_job_id)
        await self.fail(job, FailureReason.TIMEOUT)

    # ================================================================== results
    async def apply_status(self, job_id: str, status: ProviderStatus) -> None:
        """A terminal provider status (from submit, poll or webhook). Idempotent: one caller wins."""
        if not status.terminal:
            return
        job = await self._jobs.get(job_id)
        if job is None or job.status != JobStatus.RUNNING:
            return
        if status.state == ProviderState.FAILED:
            await self.fail(job, reason_for(status.error))
            return
        result = status.result
        if result is None or not result.urls:
            # "Completed" with nothing to show (e.g. silently filtered): never charge for that.
            await self.fail(job, FailureReason.PROVIDER_ERROR)
            return
        actual = result.cost_usd_actual
        claimed = await self._jobs.transition(
            job.id, (JobStatus.RUNNING,), JobStatus.DELIVERING, result_urls=result.urls, result_mime=result.mime,
            cost_usd_actual=Decimal(str(actual)) if actual is not None else job.cost_usd_estimate,
            lease_until=None, lease_owner=None,
        )
        await self._uow.commit()
        if claimed:
            await self.deliver(job.id)

    async def deliver(self, job_id: str) -> None:
        """Fetch results into our storage and send them, resuming from recorded progress. Bounded retries."""
        owner = str(uuid.uuid4())
        now = self._clock.now()
        job = await self._jobs.transition(
            job_id, (JobStatus.DELIVERING,), JobStatus.DELIVERING, conditions={"lease_free": now},
            lease_owner=owner, lease_until=self._lease(self._policy.delivery_lease_s), deliver_attempts_increment=1,
        )
        if job is None:
            return
        await self._uow.commit()
        progress = _Progress(self, job, owner)
        try:
            files = await self._ensure_stored(job, owner)
            refs = await self._channel.deliver(job, files, progress)
        except LeaseLost:
            return
        except ChannelUnreachable:
            await self._close_undeliverable(job, owner, progress, FailureReason.DELIVERY, notify=False)
            return
        except (DeliveryRejected, InvalidInput, ModerationBlocked, NotFound) as exc:
            logger.error("Delivery rejected: %s", exc, extra={"job_id": job.id})
            await self._close_undeliverable(job, owner, progress, FailureReason.DELIVERY, notify=True)
            return
        except Exception as exc:  # transient: rate limit, 5xx, network, storage hiccup
            await self._retry_delivery(job, owner, progress, exc)
            return

        done = await self._jobs.transition(
            job.id, (JobStatus.DELIVERING,), JobStatus.DELIVERED, conditions={"lease_owner": owner},
            finished_at=self._clock.now(), lease_owner=None, lease_until=None,
            result_refs={**job.result_refs, **refs, "steps": progress.steps},
        )
        if done:
            await self._capture(job)
        await self._uow.commit()

    async def _ensure_stored(self, job: Job, owner: str) -> list[MediaFile]:
        mime = job.result_mime or "application/octet-stream"
        ext = mimetypes.guess_extension(mime) or ".bin"
        keys = list(job.result_keys)
        files = []
        provider = None
        for index, url in enumerate(job.result_urls):
            key = f"results/{job.id}/{index}{ext}"
            if key in keys:
                content = await self._storage.get(key)
            else:
                provider = provider or self._registry.get(job.provider)
                content, fetched_mime = await provider.fetch(url, self._policy.max_result_bytes)
                await self._storage.put(key, content, fetched_mime or mime)
                keys.append(key)
                job = await self._fenced(job, owner, result_keys=tuple(keys))
            files.append(MediaFile(content=content, mime=mime, filename=f"{job.id[:8]}-{index}{ext}",
                                   storage_key=key))
        return files

    async def _fenced(self, job: Job, owner: str, **fields: Any) -> Job:
        """Write delivery progress only while we still own the delivery lease (and extend it)."""
        updated = await self._jobs.transition(job.id, (JobStatus.DELIVERING,), JobStatus.DELIVERING,
                                              conditions={"lease_owner": owner},
                                              lease_until=self._lease(self._policy.delivery_lease_s), **fields)
        await self._uow.commit()
        if updated is None:
            raise LeaseLost(job.id)
        return updated

    async def _retry_delivery(self, job: Job, owner: str, progress: _Progress, exc: Exception) -> None:
        if job.deliver_attempts >= self._policy.max_deliver_attempts:
            logger.error("Delivery retries exhausted: %s", exc, extra={"job_id": job.id})
            await self._close_undeliverable(job, owner, progress, FailureReason.DELIVERY, notify=True)
            return
        retry_after = getattr(exc, "retry_after_s", None)
        delay = retry_after or self._policy.deliver_backoff_s * (2 ** (job.deliver_attempts - 1))
        await self._uow.rollback()
        released = await self._jobs.transition(job.id, (JobStatus.DELIVERING,), JobStatus.DELIVERING,
                                               conditions={"lease_owner": owner}, lease_owner=None,
                                               lease_until=self._lease(delay))
        await self._uow.commit()
        logger.warning("Delivery failed (%s); retry %s in %.0fs", exc, job.deliver_attempts, delay,
                       extra={"job_id": job.id})
        if released:
            await self._queue.enqueue(DELIVER_JOB, job.id, queue=QUEUE_JOBS,
                                      job_id=f"deliver:{job.id}:{job.deliver_attempts}", defer_s=delay)

    async def _close_undeliverable(self, job: Job, owner: str, progress: _Progress, reason: FailureReason,
                                   notify: bool) -> None:
        """Give up delivering. If anything reached the user it counts as delivered; otherwise refund."""
        await self._uow.rollback()
        if progress.any_sent:
            done = await self._jobs.transition(
                job.id, (JobStatus.DELIVERING,), JobStatus.DELIVERED, conditions={"lease_owner": owner},
                error_code=f"partial_{reason.value}", finished_at=self._clock.now(), lease_owner=None,
                lease_until=None, result_refs={**job.result_refs, "steps": progress.steps},
            )
            if done:
                await self._capture(job)
            await self._uow.commit()
            return
        refunded = await self._jobs.transition(
            job.id, (JobStatus.DELIVERING,), JobStatus.REFUNDED, conditions={"lease_owner": owner},
            error_code=reason.value, finished_at=self._clock.now(), lease_owner=None, lease_until=None,
        )
        if refunded:
            await self._release(job)
        await self._uow.commit()
        if refunded and notify:
            await self._notify_failure(refunded, reason, refunded=job.credits_charged > 0)

    # ================================================================== failure
    async def fail(self, job: Job, reason: FailureReason, notify: bool = True) -> None:
        """Queued/running job failed: release the reservation (auto-refund, F11), tell the user. First caller wins."""
        refunded = job.reservation_id is not None and job.credits_charged > 0
        updated = await self._jobs.transition(
            job.id, FAILABLE_STATUSES, JobStatus.REFUNDED if refunded else JobStatus.FAILED,
            error_code=reason.value, finished_at=self._clock.now(), lease_until=None,
        )
        if updated is None:
            return
        if refunded:
            await self._release(job)
        await self._uow.commit()
        if notify:
            await self._notify_failure(updated, reason, refunded)

    async def _capture(self, job: Job) -> None:
        if job.reservation_id:
            await self._ledger.capture(job.reservation_id, commit=False)

    async def _release(self, job: Job) -> None:
        try:
            await self._ledger.release(job.reservation_id, commit=False)
        except ReservationConflict:
            # Defensive: a captured reservation means the user was charged for a delivery. Never refund twice.
            logger.critical("Refund skipped: reservation already captured", extra={"job_id": job.id})

    async def _notify_failure(self, job: Job, reason: FailureReason, refunded: bool) -> None:
        try:
            await self._channel.failed(job, reason, refunded)
        except ChannelUnreachable:
            pass
        except Exception:
            logger.warning("Failure notice not sent", extra={"job_id": job.id}, exc_info=True)

    # ================================================================== recovery
    async def recover(self, job_id: str) -> None:
        """After an unexpected error in a task: keep a running job moving (delivery recovers via its lease)."""
        await self._uow.rollback()
        job = await self._jobs.get(job_id)
        if job is not None and job.status == JobStatus.RUNNING and job.provider_job_id:
            await self.revive_polling(job)

    async def sweep(self) -> dict[str, int]:
        """Cron safety net. Acts only on expired leases; each job is isolated so one bad row can't stop the rest."""
        now = self._clock.now()
        counts = {"requeued": 0, "timed_out": 0, "redelivered": 0, "polled": 0, "errors": 0}
        for job in await self._jobs.overdue(now):
            await self._isolated(counts, "timed_out", self.timeout(job))
        for job in await self._jobs.abandoned(now):
            if job.status == JobStatus.QUEUED:
                await self._isolated(counts, "requeued", self._kick_queued(job, now))
            elif job.status == JobStatus.RUNNING and job.provider_job_id:
                await self._isolated(counts, "polled", self.revive_polling(job))
            elif job.status == JobStatus.RUNNING:
                await self._isolated(counts, "requeued", self._requeue(job, None))
            elif job.status == JobStatus.DELIVERING:
                await self._isolated(counts, "redelivered", self._kick_delivery(job, now))
        return counts

    async def _isolated(self, counts: dict[str, int], key: str, action) -> None:
        try:
            result = await action
            if result is not False:
                counts[key] += 1
        except Exception:
            counts["errors"] += 1
            logger.exception("Sweeper action failed")
            await self._uow.rollback()

    async def _kick_queued(self, job: Job, now: datetime) -> bool:
        claimed = await self._jobs.transition(job.id, (JobStatus.QUEUED,), JobStatus.QUEUED,
                                              conditions={"lease_until": job.lease_until},
                                              lease_until=self._lease(0))
        await self._uow.commit()
        if claimed:
            await self._queue.enqueue(RUN_JOB, job.id, queue=QUEUE_JOBS,
                                      job_id=f"run:{job.id}:sweep:{int(now.timestamp())}")
        return claimed is not None

    async def _kick_delivery(self, job: Job, now: datetime) -> bool:
        claimed = await self._jobs.transition(job.id, (JobStatus.DELIVERING,), JobStatus.DELIVERING,
                                              conditions={"lease_until": job.lease_until},
                                              lease_owner=None, lease_until=self._lease(0))
        await self._uow.commit()
        if claimed:
            await self._queue.enqueue(DELIVER_JOB, job.id, queue=QUEUE_JOBS,
                                      job_id=f"deliver:{job.id}:sweep:{int(now.timestamp())}")
        return claimed is not None
