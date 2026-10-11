"""Generation jobs: state machine, failure reasons, and the ports the job runner needs.

    queued ──claim──► running ──claim──► delivering ──► delivered
       │                 │                    │
       └─────────────────┴────────────────────┴──► refunded (credits released) | failed (nothing to refund)

Every transition is a conditional update (`WHERE status = <expected>`), so concurrent workers, a provider
webhook racing a poll, or a retried task can never deliver or refund twice.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol

from .generation import MediaKind, Operation


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    REFUNDED = "refunded"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in (JobStatus.DELIVERED, JobStatus.REFUNDED, JobStatus.FAILED)


ACTIVE_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.DELIVERING)
FAILABLE_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)  # never refund while a delivery is in flight


class FailureReason(str, Enum):
    MODERATION = "moderation"          # provider safety filter or our own filter
    INVALID_INPUT = "invalid_input"
    PROVIDER_DOWN = "provider_down"    # every model in the chain unavailable
    PROVIDER_BUSY = "provider_busy"    # retries exhausted
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"
    NO_MODEL = "no_model"
    DELIVERY = "delivery"              # result could not be delivered (e.g. user blocked the bot)
    BUDGET = "budget"                  # daily spend caps (guardrails)


@dataclass(frozen=True)
class Job:
    id: str
    user_id: int
    operation: Operation
    tier: str
    model_id: str
    status: JobStatus
    credits_charged: int
    prompt: str = ""
    prompt_original: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    input_keys: tuple[str, ...] = ()
    channel: str = "telegram"
    chat_ref: dict[str, Any] = field(default_factory=dict)   # opaque to the core (e.g. chat_id, message ids)
    provider: str | None = None
    provider_job_id: str | None = None
    reservation_id: str | None = None
    free_credits_used: int = 0
    watermark: bool = False
    cost_usd_estimate: Decimal = Decimal(0)
    cost_usd_actual: Decimal | None = None
    result_keys: tuple[str, ...] = ()
    result_refs: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    attempts: int = 0
    parent_job_id: str | None = None
    template_id: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    deadline_at: datetime | None = None
    heartbeat_at: datetime | None = None   # last poll / transition: the sweeper only acts on stale heartbeats

    @property
    def kind(self) -> MediaKind:
        return self.operation.kind


@dataclass(frozen=True)
class JobPolicy:
    image_timeout_s: int = 180
    video_timeout_s: int = 900
    image_poll_s: float = 2.0
    video_poll_s: float = 10.0
    max_submit_retries: int = 3
    stuck_after_s: int = 120
    max_result_bytes: int = 200 * 1024 * 1024
    webhook_base_url: str = ""          # e.g. https://bot.example.com → /webhook/provider/<name>

    def timeout_s(self, kind: MediaKind) -> int:
        return self.video_timeout_s if kind == MediaKind.VIDEO else self.image_timeout_s

    def poll_s(self, kind: MediaKind) -> float:
        return self.video_poll_s if kind == MediaKind.VIDEO else self.image_poll_s


@dataclass(frozen=True)
class MediaFile:
    content: bytes
    mime: str
    filename: str
    storage_key: str

    @property
    def kind(self) -> MediaKind:
        return MediaKind.VIDEO if self.mime.startswith("video/") else MediaKind.IMAGE


@dataclass(frozen=True)
class Progress:
    state: str                       # "queued" | "running"
    queue_position: int | None = None
    eta_s: int | None = None


class JobRepository(Protocol):
    async def add(self, job: Job) -> None: ...

    async def get(self, job_id: str) -> Job | None: ...

    async def transition(self, job_id: str, expected: tuple[JobStatus, ...], new: JobStatus,
                         **fields: Any) -> Job | None:
        """Atomically move to `new` if the current status is in `expected`; returns the updated job or None."""
        ...

    async def update(self, job_id: str, **fields: Any) -> Job: ...

    async def find_by_provider_job(self, provider: str, provider_job_id: str) -> Job | None: ...

    async def count_active(self, user_id: int) -> int: ...

    async def stale(self, status: JobStatus, older_than: datetime) -> list[Job]:
        """Jobs in `status` whose heartbeat (else created_at) is older than `older_than`."""
        ...

    async def overdue(self, now: datetime) -> list[Job]:
        """Queued or running jobs past their deadline."""
        ...

    async def queue_position(self, job: Job) -> int: ...


class DeliveryChannel(Protocol):
    """How results and failures reach the user. Implemented by the interface layer (Telegram today)."""

    async def deliver(self, job: Job, files: list[MediaFile]) -> dict[str, Any]:
        """Send results; returns channel references (e.g. Telegram file_ids) for re-sending later."""
        ...

    async def failed(self, job: Job, reason: FailureReason, refunded: bool) -> None: ...

    async def progress(self, job: Job, progress: Progress) -> None: ...


class ChannelUnreachable(Exception):
    """The user can't be reached any more (blocked the bot, chat deleted): stop trying and refund."""
