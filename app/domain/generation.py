"""Provider-agnostic generation types and the MediaProvider port (A1, plan §6.1).

Vendor parameter mapping happens inside each adapter; the core only ever sees these types.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class MediaKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


class Operation(str, Enum):
    TEXT2IMG = "text2img"
    IMG2IMG = "img2img"
    TEXT2VID = "text2vid"
    IMG2VID = "img2vid"
    FRAMES2VID = "frames2vid"      # first + last frame
    VIDEO_EXTEND = "video_extend"
    UPSCALE = "upscale"
    REMOVE_BG = "remove_bg"
    RESTORE = "restore"

    @property
    def kind(self) -> MediaKind:
        return MediaKind.VIDEO if self in VIDEO_OPERATIONS else MediaKind.IMAGE

    @property
    def needs_image(self) -> bool:
        return self not in (Operation.TEXT2IMG, Operation.TEXT2VID)


VIDEO_OPERATIONS = frozenset({Operation.TEXT2VID, Operation.IMG2VID, Operation.FRAMES2VID, Operation.VIDEO_EXTEND})


@dataclass(frozen=True)
class GenerationRequest:
    operation: Operation
    provider_model_id: str
    prompt: str = ""
    negative_prompt: str | None = None
    input_urls: tuple[str, ...] = ()
    aspect_ratio: str = "1:1"
    duration_s: int | None = None
    resolution: str | None = None
    audio: bool = False
    n: int = 1
    seed: int | None = None
    webhook_url: str | None = None

    @property
    def kind(self) -> MediaKind:
        return self.operation.kind


class ProviderState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ProviderResult:
    urls: tuple[str, ...]
    mime: str
    duration_s: float | None = None
    cost_usd_actual: Any = None  # Decimal when the vendor reports it
    raw: dict | None = field(default=None, compare=False)  # debugging only


# --------------------------------------------------------------------------- error taxonomy
class ProviderError(Exception):
    """Base of the errors the core understands; the subclass decides refund / retry / fallback."""

    def __init__(self, message: str = "", retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class Retryable(ProviderError):
    """Timeout, 5xx, rate limit: try again later with the same provider."""


class ProviderDown(ProviderError):
    """Provider unavailable: trip the breaker and try the fallback model."""


class ModerationBlocked(ProviderError):
    """Provider safety filter refused: refund and tell the user plainly."""


class InvalidInput(ProviderError):
    """The request can't be served (bad image, unsupported option): refund and show a message."""


class QuotaExhausted(ProviderError):
    """Our account is out of balance/quota: alert the operator, pause the model, try the fallback."""


@dataclass(frozen=True)
class ProviderStatus:
    state: ProviderState
    queue_position: int | None = None
    result: ProviderResult | None = None
    error: ProviderError | None = field(default=None, compare=False)

    @property
    def terminal(self) -> bool:
        return self.state in (ProviderState.COMPLETED, ProviderState.FAILED)


@dataclass(frozen=True)
class ProviderJob:
    provider_job_id: str
    status: ProviderStatus


@dataclass(frozen=True)
class ProviderEvent:
    """A normalised, signature-verified provider webhook."""

    provider_job_id: str
    status: ProviderStatus


class MediaProvider(Protocol):
    name: str

    async def submit(self, req: GenerationRequest) -> ProviderJob:
        """Start a generation. May return an already-terminal status for synchronous providers."""
        ...

    async def status(self, provider_job_id: str) -> ProviderStatus:
        """Poll fallback when the webhook is late or missing."""
        ...

    async def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> ProviderEvent | None:
        """Verify the signature and normalise. Returns None for events we don't care about.
        Raises InvalidInput when the signature is invalid."""
        ...

    async def cancel(self, provider_job_id: str) -> None: ...

    async def fetch(self, url: str, max_bytes: int) -> tuple[bytes, str]:
        """Download a result (URLs expire). Returns (content, mime)."""
        ...
