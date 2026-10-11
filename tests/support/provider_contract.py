"""Provider contract suite (plan §6.1, rule 3).

Every MediaProvider adapter must pass the same behavioural tests. Subclass `ProviderContract` (the subclass name
must start with `Test`) and implement the hooks; real vendors use recorded fixtures, FakeProvider runs live.
"""

import asyncio

import pytest

from app.domain.generation import (
    GenerationRequest,
    InvalidInput,
    MediaKind,
    MediaProvider,
    ModerationBlocked,
    ProviderError,
    ProviderEvent,
    ProviderState,
    ProviderStatus,
)

MAX_POLLS = 10


class ProviderContract:
    # ------------------------------------------------------------------ hooks
    def make_provider(self) -> MediaProvider:
        raise NotImplementedError

    def request(self, kind: MediaKind) -> GenerationRequest:
        """A request that succeeds."""
        raise NotImplementedError

    def failing_request(self) -> GenerationRequest:
        raise NotImplementedError

    def moderation_request(self) -> GenerationRequest:
        raise NotImplementedError

    def signed_webhook(self, provider: MediaProvider, provider_job_id: str, ok: bool) -> tuple[dict, bytes]:
        raise NotImplementedError

    # ------------------------------------------------------------------ helpers
    async def run_to_end(self, provider: MediaProvider, req: GenerationRequest) -> tuple[str, ProviderStatus]:
        job = await provider.submit(req)
        assert job.provider_job_id
        status = job.status
        polls = 0
        while not status.terminal:
            polls += 1
            assert polls <= MAX_POLLS, "provider never reached a terminal state"
            await asyncio.sleep(0)
            status = await provider.status(job.provider_job_id)
        return job.provider_job_id, status

    # ------------------------------------------------------------------ contract
    @pytest.mark.parametrize("kind", [MediaKind.IMAGE, MediaKind.VIDEO])
    async def test_submit_poll_and_fetch_result(self, kind):
        provider = self.make_provider()
        _, status = await self.run_to_end(provider, self.request(kind))
        assert status.state == ProviderState.COMPLETED
        assert status.result is not None and status.result.urls
        assert status.result.mime.startswith("image/" if kind == MediaKind.IMAGE else "video/")
        content, mime = await provider.fetch(status.result.urls[0], max_bytes=50 * 1024 * 1024)
        assert content and mime.split("/")[0] == status.result.mime.split("/")[0]

    async def test_failure_is_normalised_to_provider_error(self):
        provider = self.make_provider()
        try:
            _, status = await self.run_to_end(provider, self.failing_request())
        except ProviderError:
            return  # raising at submit is also a valid, normalised failure
        assert status.state == ProviderState.FAILED
        assert isinstance(status.error, ProviderError)

    async def test_moderation_block_is_normalised(self):
        provider = self.make_provider()
        try:
            _, status = await self.run_to_end(provider, self.moderation_request())
        except ModerationBlocked:
            return
        assert status.state == ProviderState.FAILED
        assert isinstance(status.error, ModerationBlocked)

    async def test_signed_webhook_is_accepted_and_normalised(self):
        provider = self.make_provider()
        job = await provider.submit(self.request(MediaKind.VIDEO))
        headers, body = self.signed_webhook(provider, job.provider_job_id, ok=True)
        event = await provider.parse_webhook(headers, body)
        assert isinstance(event, ProviderEvent)
        assert event.provider_job_id == job.provider_job_id
        assert event.status.state == ProviderState.COMPLETED and event.status.result.urls

    async def test_failed_webhook_is_normalised(self):
        provider = self.make_provider()
        job = await provider.submit(self.request(MediaKind.VIDEO))
        headers, body = self.signed_webhook(provider, job.provider_job_id, ok=False)
        event = await provider.parse_webhook(headers, body)
        assert event.status.state == ProviderState.FAILED and isinstance(event.status.error, ProviderError)

    async def test_tampered_webhook_is_rejected(self):
        provider = self.make_provider()
        job = await provider.submit(self.request(MediaKind.VIDEO))
        headers, body = self.signed_webhook(provider, job.provider_job_id, ok=True)
        with pytest.raises(InvalidInput):
            await provider.parse_webhook(headers, body.replace(b"OK", b"KO") + b" ")

    async def test_cancel_is_safe(self):
        provider = self.make_provider()
        job = await provider.submit(self.request(MediaKind.VIDEO))
        await provider.cancel(job.provider_job_id)
        await provider.cancel(job.provider_job_id)  # idempotent

    def test_has_a_stable_name(self):
        name = self.make_provider().name
        assert name and name == name.lower() and " " not in name
