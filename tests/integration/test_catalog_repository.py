from dataclasses import replace
from decimal import Decimal

from app.domain.catalog import CatalogSeed, PricingPolicy
from app.domain.generation import Operation
from app.infra.catalog_seed import load_catalog_seed
from app.infra.db import SqlUnitOfWork
from app.infra.providers.fake import FakeProvider
from app.infra.repositories.catalog import SqlCatalogRepository
from app.services.catalog import CatalogService
from app.services.providers import ProviderRegistry


def make_service(session):
    return CatalogService(SqlCatalogRepository(session), ProviderRegistry([FakeProvider()]), PricingPolicy(),
                          SqlUnitOfWork(session))


async def test_seed_roundtrip_is_lossless_and_idempotent(sessionmaker):
    seed = load_catalog_seed()
    async with sessionmaker() as session:
        svc = make_service(session)
        assert await svc.sync_seed(seed) == len(seed.models)
        assert await svc.sync_seed(seed) == 0
    async with sessionmaker() as session:
        stored = {m.id: m for m in await SqlCatalogRepository(session).list_all()}
    for original in seed.models:
        loaded = stored[original.id]
        assert loaded.capabilities == original.capabilities
        assert loaded.durations == original.durations and loaded.aspect_ratios == original.aspect_ratios
        assert loaded.unit_cost_usd == original.unit_cost_usd
        assert loaded.credits > 0


async def test_admin_edits_survive_reseeding(sessionmaker):
    seed = load_catalog_seed()
    async with sessionmaker() as session:
        svc = make_service(session)
        await svc.sync_seed(seed)
        await svc.update("img-fast-fake", {"credits": 5, "enabled": False})
        await svc.sync_seed(seed)
    async with sessionmaker() as session:
        row = await SqlCatalogRepository(session).get("img-fast-fake")
    assert (row.credits, row.credits_override, row.enabled) == (5, True, False)


async def test_provider_toggle_and_tier_resolution_against_postgres(sessionmaker):
    async with sessionmaker() as session:
        svc = make_service(session)
        await svc.sync_seed(load_catalog_seed())
        tiers = [m.tier for m in await svc.tiers(Operation.TEXT2IMG)]
        assert tiers == ["fast", "quality", "pro"]
        assert [m.id for m in await svc.chain("vid-pro-fake", Operation.IMG2VID)] == ["vid-pro-fake", "vid-hemat-fake"]
        assert await svc.set_provider_enabled("fake", False) == len(load_catalog_seed().models)
        assert await svc.tiers(Operation.TEXT2IMG) == []


async def test_save_upserts_new_rows(sessionmaker):
    seed = load_catalog_seed()
    async with sessionmaker() as session:
        repo = SqlCatalogRepository(session)
        model = replace(seed.models[0], id="custom-x", unit_cost_usd=Decimal("0.5"), credits=99)
        saved = await repo.save(model)
        await session.commit()
        assert saved == model
        assert await repo.insert_missing([]) == 0
        assert await CatalogService(repo, ProviderRegistry([]), PricingPolicy(), SqlUnitOfWork(session)).sync_seed(
            CatalogSeed()) == 0
