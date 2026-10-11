"""Ports: what the application needs from the outside world. Adapters in app.infra implement them."""

from datetime import datetime
from typing import Any, Protocol


class UnitOfWork(Protocol):
    """Transaction boundary for one request / job. Services decide when to commit."""

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class TaskQueue(Protocol):
    async def enqueue(
        self,
        task: str,
        *args: Any,
        queue: str,
        job_id: str | None = None,
        defer_s: float | None = None,
    ) -> bool:
        """Returns False when a job with the same job_id is already queued (idempotent enqueue)."""
        ...


class UpdateDeduper(Protocol):
    """Long-lived record of update_ids already handed to the queue (Telegram redelivers on errors/timeouts)."""

    async def seen(self, update_id: int) -> bool: ...

    async def mark(self, update_id: int) -> None: ...


class WizardState(Protocol):
    """Per-user multi-step flow state, plus short-id payloads for buttons (callback_data is 64 bytes max)."""

    async def get(self, user_id: int) -> dict[str, Any]: ...

    async def set(self, user_id: int, step: str | None, data: dict[str, Any] | None = None) -> None: ...

    async def update(self, user_id: int, step: str | None = ..., **data: Any) -> dict[str, Any]: ...

    async def clear(self, user_id: int) -> None: ...

    async def put_payload(self, data: dict[str, Any], ttl_s: int = ...) -> str: ...

    async def get_payload(self, pid: str) -> dict[str, Any] | None: ...


class Storage(Protocol):
    """Object storage for inputs and results (own bucket: provider URLs expire)."""

    async def put(self, key: str, data: bytes, mime: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def url(self, key: str, expires_s: int = 3600) -> str:
        """A time-limited URL a provider (or the user) can fetch without credentials."""
        ...


class SignedMediaReader(Protocol):
    """Serves storage objects behind signed URLs (only the local-disk adapter needs this)."""

    async def read_signed(self, key: str, expires: int, signature: str) -> tuple[bytes, str] | None: ...
