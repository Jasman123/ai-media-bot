"""The shipped catalog YAML is fixture data: it must be internally consistent and priced per plan §5."""

from decimal import Decimal

import pytest

from app.domain.catalog import PricingPolicy, credits_for, repriced
from app.domain.generation import GenerationRequest, MediaKind, Operation
from app.infra.catalog_seed import load_catalog_seed

SEED = load_catalog_seed()
POLICY = PricingPolicy()
BY_ID = {m.id: m for m in SEED.models}


def test_ids_are_unique():
    assert len(BY_ID) == len(SEED.models)


def test_fallbacks_exist_share_kind_and_have_no_cycles():
    for model in SEED.models:
        seen, current = set(), model
        while current.fallback_id:
            assert current.fallback_id in BY_ID, f"{current.id} → missing {current.fallback_id}"
            assert current.id not in seen, f"fallback cycle through {current.id}"
            seen.add(current.id)
            nxt = BY_ID[current.fallback_id]
            assert nxt.kind == current.kind
            current = nxt


def test_every_operation_is_served_in_development():
    served = {op for m in SEED.models if m.enabled for op in m.capabilities}
    assert served == set(Operation)


def test_capabilities_match_kind():
    for model in SEED.models:
        assert all(op.kind == model.kind for op in model.capabilities), model.id
        if model.kind == MediaKind.VIDEO:
            assert model.durations, model.id


@pytest.mark.parametrize("model_id,credits", [
    ("img-fast-fake", 2),       # plan §5 worksheet: Fast image ≈ 2 credits
    ("img-quality-fake", 19),   # Nano Banana 2 ≈ 19
    ("img-pro-fake", 35),       # Nano Banana Pro ≈ 35
])
def test_image_prices_follow_the_plan_worksheet(model_id, credits):
    assert repriced(BY_ID[model_id], POLICY).credits == credits


def test_draft_video_price_matches_plan():
    model = repriced(BY_ID["vid-hemat-fake"], POLICY)
    req = GenerationRequest(Operation.TEXT2VID, model.provider_model_id, duration_s=5)
    assert credits_for(model, req) == 35  # plan: "5 s Veo 3.1 Lite, silent (draft) = 35"


def test_unit_costs_are_positive_and_audio_costs_more():
    for model in SEED.models:
        assert model.unit_cost_usd > Decimal(0), model.id
        if model.audio_unit_cost_usd is not None:
            assert model.audio_unit_cost_usd > model.unit_cost_usd, model.id
