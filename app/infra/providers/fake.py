"""FakeProvider (A3): zero-spend provider for dev and CI.

Mode comes from the catalog's provider_model_id (`fake/<mode>`) and can be forced per request with a prompt
marker such as `[[fail]]`:

    instant     completes on submit
    delayed     queued → running → completed over `delay_polls` status calls (or a signed webhook)
    fail        accepted, then fails (non-retryable)
    moderation  accepted, then blocked by the "provider safety filter"
    down        submit raises ProviderDown        (fallback / circuit breaker)
    quota       submit raises QuotaExhausted      (operator alert, model paused)
    invalid     submit raises InvalidInput        (refund + message)
    retry       submit raises Retryable once per job request, then behaves like instant
"""

import base64
import hashlib
import hmac
import io
import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from PIL import Image, ImageDraw

from ...domain.generation import (
    GenerationRequest,
    InvalidInput,
    MediaKind,
    ModerationBlocked,
    ProviderDown,
    ProviderError,
    ProviderEvent,
    ProviderJob,
    ProviderResult,
    ProviderState,
    ProviderStatus,
    QuotaExhausted,
    Retryable,
)

MODES = {"instant", "delayed", "fail", "moderation", "down", "quota", "invalid", "retry"}
MARKER = re.compile(r"\[\[(\w+)\]\]")
SIGNATURE_HEADER = "x-fake-signature"
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64

SIZES = {"1:1": (512, 512), "4:5": (512, 640), "9:16": (288, 512), "16:9": (512, 288)}


def _mode(req: GenerationRequest) -> str:
    marker = MARKER.search(req.prompt or "")
    if marker and marker.group(1) in MODES:
        return marker.group(1)
    mode = req.provider_model_id.split("/", 1)[-1]
    return mode if mode in MODES else "instant"


def render_png(prompt: str, aspect_ratio: str, index: int = 0) -> bytes:
    size = SIZES.get(aspect_ratio, (512, 512))
    digest = hashlib.sha256(f"{prompt}|{index}".encode()).digest()
    image = Image.new("RGB", size, (digest[0], digest[1], digest[2]))
    ImageDraw.Draw(image).text((10, 10), (prompt or "fake")[:40], fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def data_url(content: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(content).decode()}"


@dataclass
class _FakeJob:
    req: GenerationRequest
    mode: str
    polls: int = 0
    cancelled: bool = False


class FakeProvider:
    name = "fake"

    def __init__(self, webhook_secret: str = "fake-webhook-secret", delay_polls: int = 2):  # noqa: S107
        self._secret = webhook_secret.encode()
        self._delay_polls = delay_polls
        self._jobs: dict[str, _FakeJob] = {}
        self._retried: set[str] = set()
        self.submitted: list[GenerationRequest] = []

    # ------------------------------------------------------------------ protocol
    async def submit(self, req: GenerationRequest) -> ProviderJob:
        mode = _mode(req)
        if mode == "down":
            raise ProviderDown("fake provider is down")
        if mode == "quota":
            raise QuotaExhausted("fake account out of balance")
        if mode == "invalid":
            raise InvalidInput("fake provider rejected the input")
        if mode == "retry":
            key = f"{req.provider_model_id}|{req.prompt}|{req.seed}"
            if key not in self._retried:
                self._retried.add(key)
                raise Retryable("fake rate limit", retry_after_s=0.01)
            mode = "instant"
        self.submitted.append(req)
        job_id = f"fake-{uuid.uuid4().hex[:12]}"
        self._jobs[job_id] = _FakeJob(req=req, mode=mode)
        if mode == "instant":
            return ProviderJob(job_id, self._completed(req))
        return ProviderJob(job_id, ProviderStatus(ProviderState.QUEUED, queue_position=1))

    async def status(self, provider_job_id: str) -> ProviderStatus:
        job = self._jobs.get(provider_job_id)
        if job is None:
            return ProviderStatus(ProviderState.FAILED, error=ProviderError("unknown fake job"))
        if job.cancelled:
            return ProviderStatus(ProviderState.FAILED, error=ProviderError("cancelled"))
        if job.mode == "fail":
            return ProviderStatus(ProviderState.FAILED, error=ProviderError("fake generation failed"))
        if job.mode == "moderation":
            return ProviderStatus(ProviderState.FAILED, error=ModerationBlocked("fake safety filter"))
        if job.mode == "instant":
            return self._completed(job.req)
        job.polls += 1
        if job.polls < self._delay_polls:
            return ProviderStatus(ProviderState.RUNNING)
        return self._completed(job.req)

    async def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> ProviderEvent | None:
        provided = {k.lower(): v for k, v in headers.items()}.get(SIGNATURE_HEADER, "")
        expected = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(provided, expected):
            raise InvalidInput("bad fake webhook signature")
        payload = json.loads(body)
        job_id = payload["job_id"]
        job = self._jobs.get(job_id)
        if payload.get("status") == "OK" and job is not None:
            return ProviderEvent(job_id, self._completed(job.req))
        if payload.get("status") == "ERROR":
            return ProviderEvent(job_id, ProviderStatus(ProviderState.FAILED,
                                                        error=ProviderError(payload.get("error", "failed"))))
        return None

    async def cancel(self, provider_job_id: str) -> None:
        job = self._jobs.get(provider_job_id)
        if job is not None:
            job.cancelled = True

    async def fetch(self, url: str, max_bytes: int) -> tuple[bytes, str]:
        if not url.startswith("data:"):
            raise InvalidInput("fake provider only serves data: URLs")
        header, encoded = url[5:].split(",", 1)
        content = base64.b64decode(encoded)
        if len(content) > max_bytes:
            raise InvalidInput("result too large")
        return content, header.split(";", 1)[0]

    # ------------------------------------------------------------------ test helpers
    def signed_webhook(self, job_id: str, ok: bool = True, error: str = "failed") -> tuple[dict[str, str], bytes]:
        body = json.dumps({"job_id": job_id, "status": "OK" if ok else "ERROR", "error": error}).encode()
        signature = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        return {SIGNATURE_HEADER: signature}, body

    def _completed(self, req: GenerationRequest) -> ProviderStatus:
        if req.kind == MediaKind.VIDEO:
            result = ProviderResult(urls=(data_url(FAKE_MP4, "video/mp4"),), mime="video/mp4",
                                    duration_s=float(req.duration_s or 5))
        else:
            urls = tuple(data_url(render_png(req.prompt, req.aspect_ratio, i), "image/png")
                         for i in range(max(1, req.n)))
            result = ProviderResult(urls=urls, mime="image/png")
        return ProviderStatus(ProviderState.COMPLETED, result=result)
