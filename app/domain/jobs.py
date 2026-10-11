"""Generation jobs: state machine, leases, failure reasons, and the ports the job runner needs.

    queued ──claim──► running ──results──► delivering ──► delivered
       │                 │                     │
       └─────────────────┴──► refunded         └──► refunded (only if nothing was ever sent)

Rules that keep money and delivery consistent under concurrency, retries and crashes:

- Every transition is a compare-and-set (`WHERE status = … [AND attempts/poll_seq/lease_owner = …]`).
- `lease_until` says until when the current owner (or plan, e.g. a scheduled poll) is responsible. The sweeper only
  touches jobs whose lease expired, and its own writes are compare-and-set too.
- Once a job is `delivering`, results exist: it can only end `delivered`, or `refunded` if not a single message
  reached the user. Deadlines/timeouts never apply to it; delivery retries are bounded instead.
- The reservation is captured only by the transaction that wins `delivering → delivered`, and released only by the
  one that wins a transition to `refunded`.
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
FAILABLE_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING)  # never refund once results exist


class FailureReason(str, Enum):
    MODERATION = "moderation"          # provider safety filter or our own filter
    INVALID_INPUT = "invalid_input"
    PROVIDER_DOWN = "provider_down"    # every model in the chain unavailable
    PROVIDER_BUSY = "provider_busy"    # retries exhausted
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"
    NO_MODEL = "no_model"
    DELIVERY = "delivery"              # result could not be delivered (user unreachable, rejected, retries out)
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
    result_urls: tuple[str, ...] = ()       # provider URLs, recorded when results arrive (outbox for delivery)
    result_mime: str | None = None
    result_keys: tuple[str, ...] = ()       # copies in our own storage, filled progressively
    result_refs: dict[str, Any] = field(default_factory=dict)  # channel refs; "steps" = delivery progress
    error_code: str | None = None
    attempts: int = 0                       # submit attempts
    deliver_attempts: int = 0
    poll_seq: int = 0                       # compare-and-set token: only one poll chain survives
    lease_until: datetime | None = None
    lease_owner: str | None = None
    parent_job_id: str | None = None
    template_id: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    deadline_at: datetime | None = None

    @property
    def kind(self) -> MediaKind:
        return self.operation.kind

    @property
    def delivery_steps(self) -> dict[str, Any]:
        return dict(self.result_refs.get("steps", {}))


@dataclass(frozen=True)
class JobPolicy:
    image_timeout_s: int = 180
    video_timeout_s: int = 900
    image_poll_s: float = 2.0
    video_poll_s: float = 10.0
    max_submit_retries: int = 3
    stuck_after_s: int = 120              # grace added to every lease
    submit_lease_s: int = 120
    delivery_lease_s: int = 300
    max_deliver_attempts: int = 8
    deliver_backoff_s: float = 5.0        # 5, 10, 20, … seconds (or Telegram's retry_after)
    max_result_bytes: int = 200 * 1024 * 1024
    webhook_base_url: str = ""            # e.g. https://bot.example.com → /webhook/provider/<name>

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
                         conditions: dict[str, Any] | None = None, **fields: Any) -> Job | None:
        """Compare-and-set; returns the updated job, or None if it wasn't in the expected state.

        `conditions` adds equality checks (`attempts`, `poll_seq`, `lease_owner`, `lease_until`) or
        `lease_free=<now>` (no owner holds the lease, or the holder's lease expired).
        `<field>_increment=n` adds to a counter."""
        ...

    async def update(self, job_id: str, **fields: Any) -> Job: ...

    async def find_by_provider_job(self, provider: str, provider_job_id: str) -> Job | None: ...

    async def count_active(self, user_id: int) -> int: ...

    async def abandoned(self, now: datetime) -> list[Job]:
        """Active jobs whose lease is unset or expired."""
        ...

    async def overdue(self, now: datetime) -> list[Job]:
        """Queued or running jobs past their deadline."""
        ...

    async def queue_position(self, job: Job) -> int: ...


class DeliveryProgress(Protocol):
    """Lets a channel deliver in idempotent steps: completed steps are persisted and skipped on retry."""

    def is_done(self, step: str) -> bool: ...

    def refs(self) -> dict[str, dict[str, Any]]:
        """Every recorded step, including those sent by earlier attempts."""
        ...

    async def mark_done(self, step: str, refs: dict[str, Any]) -> None:
        """Persist a completed step. Raises LeaseLost if another worker took the delivery over."""
        ...


class DeliveryChannel(Protocol):
    """How results and failures reach the user. Implemented by the interface layer (Telegram today)."""

    async def deliver(self, job: Job, files: list[MediaFile], progress: DeliveryProgress) -> dict[str, Any]:
        """Send results step by step; returns channel references (e.g. Telegram file_ids).
        Raises ChannelUnreachable, DeliveryRetry (transient) or DeliveryRejected (permanent)."""
        ...

    async def failed(self, job: Job, reason: FailureReason, refunded: bool) -> None: ...

    async def progress(self, job: Job, progress: Progress) -> None: ...


class ChannelUnreachable(Exception):
    """The user can't be reached any more (blocked the bot, chat deleted)."""


class DeliveryRetry(Exception):
    """Transient delivery failure (rate limit, 5xx, network). Try again later."""

    def __init__(self, message: str = "", retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class DeliveryRejected(Exception):
    """The channel will never accept this content (e.g. invalid file)."""


class LeaseLost(Exception):
    """Another worker owns this job now; stop without side effects."""
