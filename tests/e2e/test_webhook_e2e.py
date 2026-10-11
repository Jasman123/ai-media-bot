"""Webhook → queue → worker → Telegram, through the real ASGI app with its lifespan and production DI wiring."""

import httpx

from app.infra.telegram import ALLOWED_UPDATES
from app.main import create_app
from tests.conftest import API_KEY, load_fixture, make_settings
from tests.support.di import make_test_container
from tests.support.fake_telegram import FakeTelegram


async def test_start_update_gets_reply(stack):
    resp = await stack.post_update(load_fixture("telegram", "update_start.json"))
    assert resp.status_code == 200
    await stack.drain()
    assert stack.tg.last("sendMessage").chat_id == 7001001


async def test_wrong_or_missing_secret_is_rejected(stack):
    update = load_fixture("telegram", "update_start.json")
    bad = await stack.post_update(update, secret="nope")
    missing = await stack.http.post("/webhook/telegram", json=update)
    assert bad.status_code == 403 and missing.status_code == 403
    await stack.drain()
    assert stack.tg.sent("sendMessage") == []


async def test_startup_registers_webhook_with_payment_updates_and_no_drop(engine, redis_pool, clean):
    fake = FakeTelegram()
    telegram = fake.client()
    di = make_test_container(make_settings(webhook_url="https://bot.example.com/"), telegram, engine=engine,
                             redis=redis_pool)
    app = create_app(di)
    async with app.router.lifespan_context(app):
        pass
    await di.close()
    await telegram.aclose()
    call = fake.last("setWebhook")
    assert call.params["url"] == "https://bot.example.com/webhook/telegram"
    assert call.params["allowed_updates"] == ALLOWED_UPDATES
    assert call.params["drop_pending_updates"] is False
    commands = fake.sent("setMyCommands")
    assert len(commands) == 2 and commands[0].params["commands"][0]["command"] == "start"


async def test_admin_api_requires_key(stack):
    api = stack.http
    assert (await api.get("/api/health")).json() == {"status": "ok"}
    assert (await api.post("/api/send", json={"chat_id": 5, "text": "x"})).status_code == 401
    ok = await api.post("/api/send", json={"chat_id": 5, "text": "halo"}, headers={"X-API-Key": API_KEY})
    assert ok.status_code == 200
    assert stack.tg.last("sendMessage").params["text"] == "halo"
    info = await api.get("/api/webhook-info", headers={"X-API-Key": API_KEY})
    assert info.status_code == 200


async def test_module_level_app_imports_without_env():
    from app import main

    assert isinstance(main.app.router.routes, list)
    assert isinstance(httpx.ASGITransport(app=main.app), httpx.ASGITransport)
