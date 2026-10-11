"""FakeProvider runs the shared provider contract live (plan §6.1 rule 3)."""

from app.domain.generation import GenerationRequest, MediaKind, Operation
from app.infra.providers.fake import FakeProvider
from tests.support.provider_contract import ProviderContract


class TestFakeProviderContract(ProviderContract):
    def make_provider(self):
        return FakeProvider(delay_polls=2)

    def request(self, kind):
        if kind == MediaKind.IMAGE:
            return GenerationRequest(Operation.TEXT2IMG, "fake/instant", prompt="kucing batik", n=2)
        return GenerationRequest(Operation.TEXT2VID, "fake/delayed", prompt="ombak", duration_s=5,
                                 aspect_ratio="9:16")

    def failing_request(self):
        return GenerationRequest(Operation.TEXT2IMG, "fake/fail", prompt="x")

    def moderation_request(self):
        return GenerationRequest(Operation.TEXT2IMG, "fake/moderation", prompt="x")

    def signed_webhook(self, provider, provider_job_id, ok):
        return provider.signed_webhook(provider_job_id, ok=ok)
