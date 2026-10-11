"""Admin catalog API through the running app: seeded at startup, toggled and repriced without a deploy (A2)."""

from app.domain.generation import Operation
from app.services.catalog import CatalogService
from tests.conftest import API_KEY

H = {"X-API-Key": API_KEY}


async def tiers(stack, op=Operation.TEXT2IMG):
    async with stack.scope() as request:
        return [m.id for m in await (await request.get(CatalogService)).tiers(op)]


async def test_catalog_is_seeded_on_startup_and_listed(stack):
    resp = await stack.http.get("/api/admin/catalog", headers=H)
    body = resp.json()
    assert resp.status_code == 200
    assert body["providers"] == ["fake"]
    ids = {m["id"] for m in body["models"]}
    assert {"img-fast-fake", "vid-hemat-fake", "tool-upscale-fake"} <= ids
    fast = next(m for m in body["models"] if m["id"] == "img-fast-fake")
    assert fast["credits"] == 2 and fast["unit_cost_usd"] == "0.005000"


async def test_requires_api_key(stack):
    assert (await stack.http.get("/api/admin/catalog")).status_code == 401


async def test_switching_a_model_is_a_catalog_change_only(stack):
    assert await tiers(stack) == ["img-fast-fake", "img-quality-fake", "img-pro-fake"]

    off = await stack.http.patch("/api/admin/catalog/img-quality-fake", json={"enabled": False}, headers=H)
    assert off.json()["enabled"] is False
    assert await tiers(stack) == ["img-fast-fake", "img-pro-fake"]

    on = await stack.http.patch("/api/admin/catalog/img-quality-fake",
                                json={"enabled": True, "provider_model_id": "fake/fail"}, headers=H)
    assert on.json()["provider_model_id"] == "fake/fail"
    assert "img-quality-fake" in await tiers(stack)


async def test_provider_kill_switch_and_price_override(stack):
    down = await stack.http.post("/api/admin/providers/fake/disable", headers=H)
    assert down.json()["models"] >= 8
    assert await tiers(stack) == []
    await stack.http.post("/api/admin/providers/fake/enable", headers=H)
    assert len(await tiers(stack)) == 3

    pinned = await stack.http.patch("/api/admin/catalog/img-pro-fake", json={"credits": 40}, headers=H)
    assert pinned.json()["credits"] == 40 and pinned.json()["credits_override"] is True
    repriced = await stack.http.post("/api/admin/catalog/reprice", headers=H)
    by_id = {m["id"]: m for m in repriced.json()["models"]}
    assert by_id["img-pro-fake"]["credits"] == 40     # override kept
    assert by_id["img-quality-fake"]["credits"] == 19


async def test_bad_patches_are_rejected(stack):
    assert (await stack.http.patch("/api/admin/catalog/nope", json={"enabled": False}, headers=H)).status_code == 404
    assert (await stack.http.post("/api/admin/providers/fake/explode", headers=H)).status_code == 404
    bad = await stack.http.patch("/api/admin/catalog/img-fast-fake", json={"priority": "high"}, headers=H)
    assert bad.status_code == 422
