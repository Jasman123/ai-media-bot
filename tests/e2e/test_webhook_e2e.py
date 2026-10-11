"""Webhook → handler → Telegram, through the real ASGI app with its lifespan."""

import httpx

from app.container import build_container
from app.main import create_app
from app.telegram import ALLOWED_UPDATES
from tests.conftest import API_KEY, WEBHOOK_SECRET, load_fixture, make_settings
from tests.support.fake_telegram import FakeTelegram


async def test_start_update_gets_reply(api, fake_tg):
    resp = await api.post("/webhook/telegram", json=load_fixture("telegram", "update_start.json"),
                          headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET})
    assert resp.status_code == 200
    assert fake_tg.last("sendMessage").chat_id == 7001001


async def test_wrong_or_missing_secret_is_rejected(api, fake_tg):
    update = load_fixture("telegram", "update_start.json")
    bad = await api.post("/webhook/telegram", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": "nope"})
    missing = await api.post("/webhook/telegram", json=update)
    assert bad.status_code == 403 and missing.status_code == 403
    assert fake_tg.sent("sendMessage") == []


async def test_startup_registers_webhook_with_payment_updates_and_no_drop():
    fake = FakeTelegram()
    settings = make_settings(webhook_url="https://bot.example.com/")
    ctx = build_container(settings, telegram=fake.client())
    app = create_app(ctx)
    async with app.router.lifespan_context(app):
        pass
    await ctx.aclose()
    call = fake.last("setWebhook")
    assert call.params["url"] == "https://bot.example.com/webhook/telegram"
    assert call.params["allowed_updates"] == ALLOWED_UPDATES
    assert call.params["drop_pending_updates"] is False


async def test_admin_api_requires_key(api, fake_tg):
    assert (await api.get("/api/health")).json() == {"status": "ok"}
    assert (await api.post("/api/send", json={"chat_id": 5, "text": "x"})).status_code == 401
    ok = await api.post("/api/send", json={"chat_id": 5, "text": "halo"}, headers={"X-API-Key": API_KEY})
    assert ok.status_code == 200
    assert fake_tg.last("sendMessage").params["text"] == "halo"
    info = await api.get("/api/webhook-info", headers={"X-API-Key": API_KEY})
    assert info.status_code == 200


async def test_module_level_app_imports_without_env():
    from app import main

    assert isinstance(main.app.router.routes, list)
    assert isinstance(httpx.ASGITransport(app=main.app), httpx.ASGITransport)
