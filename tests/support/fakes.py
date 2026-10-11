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
