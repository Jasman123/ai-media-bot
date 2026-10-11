"""In-memory implementations of domain ports, for unit-testing services without Postgres/Redis.

Services take their dependencies through constructors, so a unit test just does:

    service = UserService(InMemoryUserRepository(), FakeUnitOfWork())
"""

import itertools
import secrets
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domain.errors import NotFound
from app.domain.users import DEFAULT_LANGUAGE, TelegramIdentity, User


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FixedClock:
    def __init__(self, now: datetime | None = None) -> None:
        self.current = now or datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.current

    def advance(self, **delta: float) -> None:
        self.current += timedelta(**delta)


class InMemoryUserRepository:
    def __init__(self) -> None:
        self.by_id: dict[int, User] = {}
        self._ids = itertools.count(1)

    async def upsert_seen(self, identity: TelegramIdentity) -> tuple[User, bool]:
        existing = await self.get_by_telegram_id(identity.telegram_id)
        if existing:
            updated = replace(existing, username=identity.username)
            self.by_id[updated.id] = updated
            return updated, False
        user = User(id=next(self._ids), telegram_id=identity.telegram_id, kind=identity.kind,
                    username=identity.username, language=DEFAULT_LANGUAGE)
        self.by_id[user.id] = user
        return user, True

    async def get(self, user_id: int) -> User | None:
        return self.by_id.get(user_id)

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return next((u for u in self.by_id.values() if u.telegram_id == telegram_id), None)

    async def set_language(self, user_id: int, language: str) -> User:
        return self._update(user_id, language=language, language_chosen=True)

    async def set_banned(self, user_id: int, banned: bool, reason: str | None) -> User:
        return self._update(user_id, is_banned=banned, ban_reason=reason)

    def _update(self, user_id: int, **values: Any) -> User:
        if user_id not in self.by_id:
            raise NotFound(str(user_id))
        self.by_id[user_id] = replace(self.by_id[user_id], **values)
        return self.by_id[user_id]


class InMemoryWizardState:
    def __init__(self) -> None:
        self.states: dict[int, dict[str, Any]] = {}
        self.payloads: dict[str, dict[str, Any]] = {}

    async def get(self, user_id: int) -> dict[str, Any]:
        state = self.states.get(user_id, {"step": None, "data": {}})
        return {"step": state["step"], "data": dict(state["data"])}

    async def set(self, user_id: int, step: str | None, data: dict[str, Any] | None = None) -> None:
        self.states[user_id] = {"step": step, "data": dict(data or {})}

    async def update(self, user_id: int, step: str | None = ..., **data: Any) -> dict[str, Any]:
        state = await self.get(user_id)
        if step is not ...:
            state["step"] = step
        state["data"].update(data)
        self.states[user_id] = state
        return state

    async def clear(self, user_id: int) -> None:
        self.states.pop(user_id, None)

    async def put_payload(self, data: dict[str, Any], ttl_s: int = 0) -> str:
        pid = secrets.token_urlsafe(6)
        self.payloads[pid] = data
        return pid

    async def get_payload(self, pid: str) -> dict[str, Any] | None:
        return self.payloads.get(pid)


class InMemoryDeduper:
    def __init__(self, fail_mark: bool = False) -> None:
        self.ids: set[int] = set()
        self.fail_mark = fail_mark

    async def seen(self, update_id: int) -> bool:
        return update_id in self.ids

    async def mark(self, update_id: int) -> None:
        if self.fail_mark:
            raise ConnectionError("redis blip")
        self.ids.add(update_id)


class RecordingTaskQueue:
    def __init__(self, fail: bool = False) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.fail = fail

    async def enqueue(self, task: str, *args: Any, queue: str, job_id: str | None = None,
                      defer_s: float | None = None) -> bool:
        if self.fail:
            raise ConnectionError("queue down")
        if job_id and any(j["job_id"] == job_id for j in self.jobs):
            return False
        self.jobs.append({"task": task, "args": args, "queue": queue, "job_id": job_id, "defer_s": defer_s})
        return True


class InMemoryCatalogRepository:
    def __init__(self, models=()) -> None:
        self.models = {m.id: m for m in models}

    async def list_all(self):
        return list(self.models.values())

    async def get(self, model_id):
        return self.models.get(model_id)

    async def insert_missing(self, models) -> int:
        added = 0
        for model in models:
            if model.id not in self.models:
                self.models[model.id] = model
                added += 1
        return added

    async def save(self, model):
        self.models[model.id] = model
        return model

    async def set_provider_enabled(self, provider, enabled) -> int:
        changed = 0
        for model_id, model in list(self.models.items()):
            if model.provider == provider:
                self.models[model_id] = replace(model, enabled=enabled)
                changed += 1
        return changed


class InMemoryLedgerRepository:
    """Mirrors SqlLedgerRepository semantics (idempotency keys, FIFO-expiry query, reservations)."""

    def __init__(self) -> None:
        from app.domain.billing import NewEntry  # noqa: F401  (documented type of self.entries)

        self.entries: list[tuple[int, Any]] = []      # (id, NewEntry)
        self.reservations: dict[str, Any] = {}
        self.locks: list[int] = []
        self._ids = itertools.count(1)
        self.now = datetime(2026, 10, 11, tzinfo=timezone.utc)

    async def lock_account(self, user_id: int) -> None:
        self.locks.append(user_id)

    async def balance(self, user_id: int):
        from app.domain.billing import Balance, Bucket

        free = sum(e.amount for _, e in self.entries if e.user_id == user_id and e.bucket == Bucket.FREE)
        paid = sum(e.amount for _, e in self.entries if e.user_id == user_id and e.bucket == Bucket.PAID)
        return Balance(free=free, paid=paid)

    async def append(self, entries) -> int:
        inserted = 0
        for entry in entries:
            if entry.idempotency_key and await self.has_key(entry.idempotency_key):
                continue
            self.entries.append((next(self._ids), entry))
            inserted += 1
        return inserted

    async def has_key(self, idempotency_key: str) -> bool:
        return any(e.idempotency_key == idempotency_key for _, e in self.entries)

    async def unexpired_free_grants(self, user_id: int, now: datetime) -> int:
        from app.domain.billing import Bucket, EntryKind

        return sum(e.amount for _, e in self.entries
                   if e.user_id == user_id and e.bucket == Bucket.FREE and e.amount > 0
                   and e.kind != EntryKind.RELEASE and (e.expires_at is None or e.expires_at > now))

    async def next_free_expiry(self, user_id: int, now: datetime):
        from app.domain.billing import Bucket

        dates = [e.expires_at for _, e in self.entries if e.user_id == user_id and e.bucket == Bucket.FREE
                 and e.amount > 0 and e.expires_at and e.expires_at > now]
        return min(dates) if dates else None

    async def accounts_with_free_credits(self) -> list[int]:
        users = sorted({e.user_id for _, e in self.entries})
        return [u for u in users if (await self.balance(u)).free > 0]

    async def create_reservation(self, reservation) -> None:
        self.reservations[reservation.id] = reservation

    async def get_reservation(self, reservation_id: str, for_update: bool = False):
        return self.reservations.get(reservation_id)

    async def set_reservation_status(self, reservation_id, status, at) -> None:
        self.reservations[reservation_id] = replace(self.reservations[reservation_id], status=status)

    async def history(self, user_id: int, limit: int = 20):
        from app.domain.billing import LedgerEntry

        rows = [(i, e) for i, e in self.entries if e.user_id == user_id and e.amount != 0][::-1][:limit]
        return [LedgerEntry(id=i, user_id=e.user_id, bucket=e.bucket, amount=e.amount, kind=e.kind,
                            created_at=self.now, reservation_id=e.reservation_id, job_id=e.job_id,
                            expires_at=e.expires_at, note=e.note) for i, e in rows]


class InMemoryJobRepository:
    def __init__(self) -> None:
        self.jobs: dict[str, Any] = {}

    async def add(self, job) -> None:
        self.jobs[job.id] = job

    async def get(self, job_id: str):
        return self.jobs.get(job_id)

    @staticmethod
    def _apply(job, fields):
        fields = dict(fields)
        for counter in ("attempts", "deliver_attempts"):
            if f"{counter}_increment" in fields:
                fields[counter] = getattr(job, counter) + fields.pop(f"{counter}_increment")
        for key in ("input_keys", "result_keys", "result_urls"):
            if key in fields:
                fields[key] = tuple(fields[key])
        return replace(job, **fields)

    @staticmethod
    def _matches(job, conditions) -> bool:
        for key, value in (conditions or {}).items():
            if key == "lease_free":
                held = job.lease_owner is not None and job.lease_until is not None and job.lease_until >= value
                if held:
                    return False
            elif getattr(job, key) != value:
                return False
        return True

    async def transition(self, job_id, expected, new, conditions=None, **fields):
        job = self.jobs.get(job_id)
        if job is None or job.status not in expected or not self._matches(job, conditions):
            return None
        self.jobs[job_id] = self._apply(job, {**fields, "status": new})
        return self.jobs[job_id]

    async def update(self, job_id, **fields):
        if job_id not in self.jobs:
            raise NotFound(job_id)
        self.jobs[job_id] = self._apply(self.jobs[job_id], fields)
        return self.jobs[job_id]

    async def find_by_provider_job(self, provider, provider_job_id):
        return next((j for j in self.jobs.values()
                     if j.provider == provider and j.provider_job_id == provider_job_id), None)

    async def count_active(self, user_id: int) -> int:
        from app.domain.jobs import ACTIVE_STATUSES

        return sum(1 for j in self.jobs.values() if j.user_id == user_id and j.status in ACTIVE_STATUSES)

    async def abandoned(self, now):
        from app.domain.jobs import ACTIVE_STATUSES

        return [j for j in self.jobs.values()
                if j.status in ACTIVE_STATUSES and (j.lease_until is None or j.lease_until < now)]

    async def overdue(self, now):
        from app.domain.jobs import FAILABLE_STATUSES

        return [j for j in self.jobs.values() if j.status in FAILABLE_STATUSES and j.deadline_at
                and j.deadline_at < now]

    async def queue_position(self, job) -> int:
        from app.domain.jobs import JobStatus

        return 1 + sum(1 for j in self.jobs.values() if j.status in (JobStatus.QUEUED, JobStatus.RUNNING)
                       and j.created_at < job.created_at)


class InMemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, data: bytes, mime: str) -> None:
        self.objects[key] = (data, mime)

    async def get(self, key: str) -> bytes:
        return self.objects[key][0]

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def url(self, key: str, expires_s: int = 3600) -> str:
        return f"https://storage.test/{key}?ttl={expires_s}"


class RecordingChannel:
    """DeliveryChannel fake: one step per file. `error` is raised before the first unsent step; with
    `fail_after` it is raised after that many new steps (a partial delivery)."""

    def __init__(self, error: Exception | None = None, fail_after: int | None = None) -> None:
        self.delivered: list[tuple[Any, list]] = []
        self.sent_steps: list[str] = []
        self.failures: list[tuple[Any, Any, bool]] = []
        self.progress_updates: list[tuple[Any, Any]] = []
        self.error = error
        self.fail_after = fail_after

    async def deliver(self, job, files, progress):
        new_steps = 0
        for index, _ in enumerate(files):
            step = f"file:{index}"
            if progress.is_done(step):
                continue
            if self.error is not None and (self.fail_after is None or new_steps >= self.fail_after):
                raise self.error
            await progress.mark_done(step, {"file_ids": [f"fid-{index}"]})
            self.sent_steps.append(step)
            new_steps += 1
        self.delivered.append((job, files))
        return {"file_ids": [fid for refs in progress.refs().values() for fid in refs["file_ids"]]}

    async def failed(self, job, reason, refunded) -> None:
        self.failures.append((job, reason, refunded))

    async def progress(self, job, progress) -> None:
        self.progress_updates.append((job, progress))
