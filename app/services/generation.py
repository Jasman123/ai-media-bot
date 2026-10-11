"""Creating generation jobs: price from the catalog, reserve credits, persist, enqueue (F11 cost preview lives here)."""

import uuid
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any

from ..domain.billing import free_allowed
from ..domain.generation import GenerationRequest, Operation
from ..domain.jobs import Job, JobPolicy, JobRepository, JobStatus
from ..domain.ports import Clock, TaskQueue, UnitOfWork
from ..domain.tasks import QUEUE_JOBS, RUN_JOB
from .catalog import CatalogService, Quote
from .ledger import LedgerService


@dataclass(frozen=True)
class JobDraft:
    """What the interface collected from the user. Prices are never taken from here: always from the catalog."""

    operation: Operation
    tier: str
    prompt: str = ""
    prompt_original: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    input_keys: tuple[str, ...] = ()
    channel: str = "telegram"
    chat_ref: dict[str, Any] = field(default_factory=dict)
    parent_job_id: str | None = None
    template_id: str | None = None

    def request(self, provider_model_id: str = "") -> GenerationRequest:
        p = self.params
        return GenerationRequest(
            operation=self.operation, provider_model_id=provider_model_id, prompt=self.prompt,
            aspect_ratio=p.get("aspect_ratio", "1:1"), duration_s=p.get("duration_s"), audio=bool(p.get("audio")),
            n=int(p.get("n", 1)), seed=p.get("seed"), resolution=p.get("resolution"),
            negative_prompt=p.get("negative_prompt"),
        )


class GenerationService:
    def __init__(self, jobs: JobRepository, catalog: CatalogService, ledger: LedgerService, queue: TaskQueue,
                 uow: UnitOfWork, clock: Clock, policy: JobPolicy):
        self._jobs = jobs
        self._catalog = catalog
        self._ledger = ledger
        self._queue = queue
        self._uow = uow
        self._clock = clock
        self._policy = policy

    async def quote(self, draft: JobDraft) -> Quote:
        """Cost preview shown before anything is spent (F11)."""
        return await self._catalog.quote(draft.tier, draft.request())

    async def create(self, user_id: int, draft: JobDraft) -> Job:
        """Reserve credits and queue the job. Raises InsufficientCredits / NoModelAvailable; nothing is charged then."""
        quote = await self.quote(draft)
        job_id = str(uuid.uuid4())
        reservation = await self._ledger.reserve(user_id, quote.credits, allow_free=free_allowed(draft.operation.kind),
                                                 job_id=job_id, commit=False)
        now = self._clock.now()
        job = Job(
            id=job_id, user_id=user_id, operation=draft.operation, tier=draft.tier, model_id=quote.model.id,
            status=JobStatus.QUEUED, credits_charged=quote.credits, prompt=draft.prompt or draft.prompt_original,
            prompt_original=draft.prompt_original or draft.prompt, params=dict(draft.params),
            input_keys=draft.input_keys, channel=draft.channel, chat_ref=dict(draft.chat_ref),
            reservation_id=reservation.id, free_credits_used=reservation.free_amount,
            watermark=reservation.free_amount > 0, cost_usd_estimate=quote.cost_usd,
            parent_job_id=draft.parent_job_id, template_id=draft.template_id, created_at=now,
            deadline_at=now + timedelta(seconds=self._policy.timeout_s(draft.operation.kind)),
            # If the enqueue below is lost, the sweeper picks the job up once this lease expires.
            lease_until=now + timedelta(seconds=self._policy.stuck_after_s),
        )
        await self._jobs.add(job)
        await self._uow.commit()
        # After commit: the worker must be able to read the job. If this enqueue is lost, the sweeper requeues it.
        await self._queue.enqueue(RUN_JOB, job.id, queue=QUEUE_JOBS, job_id=f"run:{job.id}:0")
        return job

    async def get(self, job_id: str) -> Job | None:
        return await self._jobs.get(job_id)

    async def set_chat_ref(self, job_id: str, **values: Any) -> Job:
        job = await self._jobs.get(job_id)
        updated = await self._jobs.update(job_id, chat_ref={**job.chat_ref, **values})
        await self._uow.commit()
        return updated

    async def queue_position(self, job: Job) -> int:
        return await self._jobs.queue_position(job)


def with_params(draft: JobDraft, **params: Any) -> JobDraft:
    return replace(draft, params={**draft.params, **params})
