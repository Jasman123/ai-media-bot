"""Builds GenerationService + JobRunner wired entirely with in-memory fakes and the live FakeProvider."""

from dataclasses import dataclass

from app.domain.billing import Bucket, EntryKind
from app.domain.catalog import PricingPolicy
from app.domain.jobs import JobPolicy
from app.infra.catalog_seed import load_catalog_seed
from app.infra.providers.fake import FakeProvider
from app.services.catalog import CatalogService
from app.services.generation import GenerationService
from app.services.job_runner import JobRunner
from app.services.ledger import LedgerService
from app.services.providers import ProviderRegistry
from tests.support.fakes import (
    FakeUnitOfWork,
    FixedClock,
    InMemoryCatalogRepository,
    InMemoryJobRepository,
    InMemoryLedgerRepository,
    InMemoryStorage,
    RecordingChannel,
    RecordingTaskQueue,
)


@dataclass
class Pipeline:
    generation: GenerationService
    runner: JobRunner
    ledger: LedgerService
    catalog: CatalogService
    jobs: InMemoryJobRepository
    ledger_repo: InMemoryLedgerRepository
    catalog_repo: InMemoryCatalogRepository
    queue: RecordingTaskQueue
    storage: InMemoryStorage
    channel: RecordingChannel
    provider: FakeProvider
    clock: FixedClock
    policy: JobPolicy

    async def fund(self, user_id: int = 1, paid: int = 0, free: int = 0) -> None:
        if paid:
            await self.ledger.grant(user_id, paid, Bucket.PAID, EntryKind.PURCHASE)
        if free:
            await self.ledger.grant(user_id, free, Bucket.FREE, EntryKind.SIGNUP_GIFT)

    async def balance(self, user_id: int = 1):
        return await self.ledger_repo.balance(user_id)

    async def drain(self, max_steps: int = 50) -> None:
        """Execute queued tasks in order (deferral ignored), like a worker would."""
        steps = 0
        while self.queue.jobs:
            steps += 1
            assert steps <= max_steps, "pipeline did not settle"
            task = self.queue.jobs.pop(0)
            name, args = task["task"], task["args"]
            if name == "run_job":
                await self.runner.run(*args)
            elif name == "poll_job":
                await self.runner.poll(*args)
            elif name == "apply_provider_status":
                await self.runner.apply_status(*args)
            else:
                raise AssertionError(f"unexpected task {name}")


async def build_pipeline(policy: JobPolicy | None = None, channel: RecordingChannel | None = None,
                         delay_polls: int = 2, seed=None, pricing: PricingPolicy | None = None) -> Pipeline:
    clock = FixedClock()
    pricing = pricing or PricingPolicy()
    policy = policy or JobPolicy(image_poll_s=0, video_poll_s=0)
    provider = FakeProvider(delay_polls=delay_polls)
    registry = ProviderRegistry([provider])
    uow = FakeUnitOfWork()
    catalog_repo = InMemoryCatalogRepository()
    catalog = CatalogService(catalog_repo, registry, pricing, uow)
    await catalog.sync_seed(seed or load_catalog_seed())
    ledger_repo = InMemoryLedgerRepository()
    ledger = LedgerService(ledger_repo, uow, clock)
    jobs, queue, storage = InMemoryJobRepository(), RecordingTaskQueue(), InMemoryStorage()
    channel = channel or RecordingChannel()
    generation = GenerationService(jobs, catalog, ledger, queue, uow, clock, policy)
    runner = JobRunner(jobs, catalog, ledger, registry, queue, storage, channel, uow, clock, policy, pricing)
    return Pipeline(generation, runner, ledger, catalog, jobs, ledger_repo, catalog_repo, queue, storage, channel,
                    provider, clock, policy)
