from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.errors import NotFound
from ...domain.generation import Operation
from ...domain.jobs import FAILABLE_STATUSES, Job, JobStatus
from ..models import JobRow

TUPLE_FIELDS = ("input_keys", "result_keys")


def to_job(row: JobRow) -> Job:
    return Job(
        id=row.id, user_id=row.user_id, operation=Operation(row.operation), tier=row.tier, model_id=row.model_id,
        status=JobStatus(row.status), credits_charged=row.credits_charged, prompt=row.prompt,
        prompt_original=row.prompt_original, params=dict(row.params or {}), input_keys=tuple(row.input_keys or ()),
        channel=row.channel, chat_ref=dict(row.chat_ref or {}), provider=row.provider,
        provider_job_id=row.provider_job_id, reservation_id=row.reservation_id,
        free_credits_used=row.free_credits_used, watermark=row.watermark,
        cost_usd_estimate=Decimal(row.cost_usd_estimate or 0), cost_usd_actual=row.cost_usd_actual,
        result_keys=tuple(row.result_keys or ()), result_refs=dict(row.result_refs or {}),
        error_code=row.error_code, attempts=row.attempts, parent_job_id=row.parent_job_id,
        template_id=row.template_id, created_at=row.created_at, started_at=row.started_at,
        finished_at=row.finished_at, deadline_at=row.deadline_at, heartbeat_at=row.heartbeat_at,
    )


def _values(fields: dict[str, Any]) -> dict[str, Any]:
    values = {}
    for key, value in fields.items():
        if key == "attempts_increment":
            values["attempts"] = JobRow.attempts + value
        elif key == "status" and isinstance(value, JobStatus):
            values[key] = value.value
        elif key == "operation":
            values[key] = value.value
        elif key in TUPLE_FIELDS:
            values[key] = list(value)
        else:
            values[key] = value
    return values


class SqlJobRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(self, job: Job) -> None:
        self._session.add(JobRow(
            id=job.id, user_id=job.user_id, operation=job.operation.value, tier=job.tier, model_id=job.model_id,
            status=job.status.value, credits_charged=job.credits_charged, free_credits_used=job.free_credits_used,
            prompt=job.prompt, prompt_original=job.prompt_original, params=job.params, input_keys=list(job.input_keys),
            channel=job.channel, chat_ref=job.chat_ref, provider=job.provider, provider_job_id=job.provider_job_id,
            reservation_id=job.reservation_id, watermark=job.watermark, cost_usd_estimate=job.cost_usd_estimate,
            result_keys=list(job.result_keys), result_refs=job.result_refs, parent_job_id=job.parent_job_id,
            template_id=job.template_id, deadline_at=job.deadline_at, heartbeat_at=job.created_at,
            **({"created_at": job.created_at} if job.created_at else {}),
        ))
        await self._session.flush()

    async def get(self, job_id: str) -> Job | None:
        row = await self._session.get(JobRow, job_id, populate_existing=True)
        return to_job(row) if row else None

    async def transition(self, job_id: str, expected: tuple[JobStatus, ...], new: JobStatus,
                         **fields: Any) -> Job | None:
        row = await self._session.scalar(
            update(JobRow)
            .where(JobRow.id == job_id, JobRow.status.in_([s.value for s in expected]))
            .values(status=new.value, **_values(fields))
            .returning(JobRow)
            .execution_options(populate_existing=True, synchronize_session=False)
        )
        return to_job(row) if row else None

    async def update(self, job_id: str, **fields: Any) -> Job:
        row = await self._session.scalar(
            update(JobRow).where(JobRow.id == job_id).values(**_values(fields)).returning(JobRow)
            .execution_options(populate_existing=True, synchronize_session=False)
        )
        if row is None:
            raise NotFound(f"job {job_id}")
        return to_job(row)

    async def find_by_provider_job(self, provider: str, provider_job_id: str) -> Job | None:
        row = await self._session.scalar(
            select(JobRow).where(JobRow.provider == provider, JobRow.provider_job_id == provider_job_id)
            .execution_options(populate_existing=True)
        )
        return to_job(row) if row else None

    async def count_active(self, user_id: int) -> int:
        return await self._session.scalar(
            select(func.count()).where(JobRow.user_id == user_id,
                                       JobRow.status.in_(["queued", "running", "delivering"]))
        )

    async def stale(self, status: JobStatus, older_than: datetime) -> list[Job]:
        rows = await self._session.scalars(
            select(JobRow).where(JobRow.status == status.value,
                                 func.coalesce(JobRow.heartbeat_at, JobRow.created_at) < older_than)
            .order_by(JobRow.created_at).limit(200).execution_options(populate_existing=True)
        )
        return [to_job(r) for r in rows]

    async def overdue(self, now: datetime) -> list[Job]:
        rows = await self._session.scalars(
            select(JobRow).where(JobRow.status.in_([s.value for s in FAILABLE_STATUSES]), JobRow.deadline_at < now)
            .limit(200).execution_options(populate_existing=True)
        )
        return [to_job(r) for r in rows]

    async def queue_position(self, job: Job) -> int:
        """1-based position among jobs still waiting or running ahead of this one (F14)."""
        ahead = await self._session.scalar(
            select(func.count()).where(
                JobRow.status.in_(["queued", "running"]),
                and_(JobRow.created_at < job.created_at) if job.created_at else JobRow.id != job.id,
            )
        )
        return int(ahead) + 1
