from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.catalog import (
    CatalogModel,
    CatalogSeed,
    CostModel,
    PricingPolicy,
    credits_for,
    estimate_cost_usd,
    margin_ok,
    repriced,
)
from app.domain.errors import NoModelAvailable, NotFound
from app.domain.generation import (
    GenerationRequest,
    InvalidInput,
    MediaKind,
    ModerationBlocked,
    Operation,
    ProviderDown,
    ProviderState,
    QuotaExhausted,
    Retryable,
)
from app.infra.providers.fake import FakeProvider
from app.services.catalog import CatalogService
from app.services.providers import ProviderRegistry
from tests.support.fakes import FakeUnitOfWork, InMemoryCatalogRepository


def model(id="m", tier="fast", provider="fake", kind=MediaKind.IMAGE, ops=(Operation.TEXT2IMG,), cost="0.005",
          credits=2, **kw) -> CatalogModel:
    return CatalogModel(id=id, kind=kind, tier=tier, provider=provider, provider_model_id="fake/instant",
                        capabilities=frozenset(ops), cost_model=kw.pop("cost_model", CostModel.PER_IMAGE),
                        unit_cost_usd=Decimal(cost), credits=credits, **kw)


# ---------------------------------------------------------------------------- pricing
@pytest.mark.parametrize("cost,expected", [("0.005", 2), ("0.035", 9), ("0.08", 19), ("0.15", 35), ("0", 0)])
def test_credits_for_cost_matches_plan_worksheet(cost, expected):
    assert PricingPolicy().credits_for_cost(Decimal(cost)) == expected


def test_per_second_pricing_with_audio_and_duration():
    video = model(kind=MediaKind.VIDEO, ops=(Operation.TEXT2VID,), cost_model=CostModel.PER_SECOND, cost="0.084",
                  credits=20, audio_unit_cost_usd=Decimal("0.126"), audio_credits=30, durations=(5, 10))
    silent = GenerationRequest(Operation.TEXT2VID, "x", duration_s=10)
    loud = replace(silent, audio=True)
    assert credits_for(video, silent) == 200 and credits_for(video, loud) == 300
    assert estimate_cost_usd(video, silent) == Decimal("0.840")
    assert estimate_cost_usd(video, loud) == Decimal("1.260")
    assert credits_for(video, replace(silent, duration_s=None)) == 100  # first allowed duration


def test_per_image_and_per_megapixel_and_per_job():
    img = model(credits=2)
    assert credits_for(img, GenerationRequest(Operation.TEXT2IMG, "x", n=4)) == 8
    mp = model(cost_model=CostModel.PER_MEGAPIXEL, cost="0.004")
    assert estimate_cost_usd(mp, GenerationRequest(Operation.TEXT2IMG, "x", resolution="2048x1024")) == \
        Decimal("0.004") * Decimal("2.097152")
    tool = model(cost_model=CostModel.PER_JOB, cost="0.01", credits=3)
    assert credits_for(tool, GenerationRequest(Operation.UPSCALE, "x", n=3)) == 3


def test_margin_floor_for_fallbacks():
    policy = PricingPolicy()
    assert margin_ok(policy, 19, Decimal("0.08"))       # 19 × 0.013 = 0.247 ≥ 1.5 × 0.08
    assert not margin_ok(policy, 2, Decimal("0.08"))    # a fast-tier price can't pay for a quality model
    assert margin_ok(policy, 2, Decimal("0"))


def test_reprice_respects_manual_override():
    policy = PricingPolicy(markup=Decimal("2"))
    assert repriced(model(cost="0.08", credits=1), policy).credits == 13
    pinned = model(cost="0.08", credits=1, credits_override=True)
    assert repriced(pinned, policy) == pinned


# ---------------------------------------------------------------------------- catalog service
def service(*models, providers=("fake",)):
    class P:
        def __init__(self, name):
            self.name = name

    repo = InMemoryCatalogRepository(models)
    return CatalogService(repo, ProviderRegistry([P(n) for n in providers]), PricingPolicy(), FakeUnitOfWork()), repo


async def test_tiers_pick_primary_by_priority_and_skip_unusable():
    svc, _ = service(
        model("fast-a", "fast", priority=10, credits=2),
        model("fast-b", "fast", priority=5, credits=3),
        model("pro", "pro", credits=35),
        model("off", "quality", enabled=False, credits=19),
        model("ghost", "quality", provider="unregistered", credits=19),
        model("vid", "hemat", kind=MediaKind.VIDEO, ops=(Operation.TEXT2VID,), credits=7),
    )
    tiers = await svc.tiers(Operation.TEXT2IMG)
    assert [(m.tier, m.id) for m in tiers] == [("fast", "fast-b"), ("pro", "pro")]
    assert (await svc.primary("pro", Operation.TEXT2IMG)).id == "pro"
    with pytest.raises(NoModelAvailable):
        await svc.primary("quality", Operation.TEXT2IMG)


async def test_chain_follows_fallbacks_skips_disabled_and_survives_cycles():
    svc, _ = service(
        model("a", fallback_id="b"), model("b", enabled=False, fallback_id="c"), model("c", fallback_id="a"),
    )
    assert [m.id for m in await svc.chain("a", Operation.TEXT2IMG)] == ["a", "c"]
    assert await svc.chain("missing", Operation.TEXT2IMG) == []


async def test_quote_prices_from_tier_primary():
    svc, _ = service(model("q", "quality", cost="0.08", credits=19, max_n=4))
    quote = await svc.quote("quality", GenerationRequest(Operation.TEXT2IMG, "", n=2))
    assert (quote.model.id, quote.credits, quote.cost_usd) == ("q", 38, Decimal("0.16"))


async def test_update_pins_price_and_rejects_unknown_fields():
    svc, repo = service(model("q", cost="0.08", credits=19))
    updated = await svc.update("q", {"credits": 25})
    assert updated.credits == 25 and updated.credits_override
    cleared = await svc.update("q", {"credits_override": False})
    assert cleared.credits == 19
    cost_change = await svc.update("q", {"unit_cost_usd": "0.15"})
    assert cost_change.credits == 35
    with pytest.raises(ValueError):
        await svc.update("q", {"id": "hack"})
    with pytest.raises(NotFound):
        await svc.update("nope", {"enabled": False})


async def test_provider_toggle_pause_reprice_and_seed():
    svc, repo = service(model("a", credits=1), model("b", provider="other", credits=1))
    assert await svc.set_provider_enabled("fake", False) == 1
    assert not repo.models["a"].enabled and repo.models["b"].enabled
    await svc.set_provider_enabled("fake", True)
    await svc.pause("a")
    assert not repo.models["a"].enabled and "auto-paused" in repo.models["a"].notes
    repriced_models = await svc.reprice()
    assert {m.id: m.credits for m in repriced_models} == {"a": 2, "b": 2}
    seed = CatalogSeed((model("a", credits=99), model("new", cost="0.15", credits=0)))
    assert await svc.sync_seed(seed) == 1
    assert repo.models["a"].credits == 2           # existing row untouched
    assert repo.models["new"].credits == 35        # new row priced by policy


def test_registry_rejects_duplicates_and_unknown_names():
    with pytest.raises(ValueError):
        ProviderRegistry([FakeProvider(), FakeProvider()])
    registry = ProviderRegistry([FakeProvider()])
    assert registry.names == ["fake"] and registry.has("fake")
    with pytest.raises(NotFound):
        registry.get("fal")


# ---------------------------------------------------------------------------- fake provider modes
@pytest.mark.parametrize("mode,exc", [("down", ProviderDown), ("quota", QuotaExhausted), ("invalid", InvalidInput)])
async def test_fake_submit_errors(mode, exc):
    with pytest.raises(exc):
        await FakeProvider().submit(GenerationRequest(Operation.TEXT2IMG, f"fake/{mode}", prompt="x"))


async def test_fake_prompt_marker_overrides_model_mode():
    provider = FakeProvider()
    job = await provider.submit(GenerationRequest(Operation.TEXT2IMG, "fake/instant", prompt="x [[moderation]]"))
    status = await provider.status(job.provider_job_id)
    assert status.state == ProviderState.FAILED and isinstance(status.error, ModerationBlocked)


async def test_fake_retry_mode_raises_once_then_succeeds():
    provider = FakeProvider()
    req = GenerationRequest(Operation.TEXT2IMG, "fake/retry", prompt="x")
    with pytest.raises(Retryable):
        await provider.submit(req)
    assert (await provider.submit(req)).status.state == ProviderState.COMPLETED


async def test_fake_delayed_progresses_and_cancel_fails_job():
    provider = FakeProvider(delay_polls=3)
    job = await provider.submit(GenerationRequest(Operation.TEXT2VID, "fake/delayed", duration_s=5))
    assert job.status.state == ProviderState.QUEUED
    states = [(await provider.status(job.provider_job_id)).state for _ in range(3)]
    assert states == [ProviderState.RUNNING, ProviderState.RUNNING, ProviderState.COMPLETED]
    other = await provider.submit(GenerationRequest(Operation.TEXT2VID, "fake/delayed", duration_s=5))
    await provider.cancel(other.provider_job_id)
    assert (await provider.status(other.provider_job_id)).state == ProviderState.FAILED


async def test_fake_images_are_real_pngs_sized_by_aspect():
    from io import BytesIO

    from PIL import Image

    provider = FakeProvider()
    job = await provider.submit(GenerationRequest(Operation.TEXT2IMG, "fake/instant", prompt="p", aspect_ratio="9:16"))
    content, mime = await provider.fetch(job.status.result.urls[0], max_bytes=10_000_000)
    assert mime == "image/png"
    assert Image.open(BytesIO(content)).size == (288, 512)
    with pytest.raises(InvalidInput):
        await provider.fetch("https://example.com/x.png", max_bytes=10)
