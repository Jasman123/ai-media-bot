"""Postgres, Alembic and Redis/ARQ with real services."""

import asyncio

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select

from app.container import build_container
from app.main import create_app
from app.models import Base, User
from app.queue import QUEUE_UPDATES
from app.services.users import get_or_create_user
from app.state import StateStore, UpdateDeduper
from tests.support import updates


async def test_migrations_match_models(engine, clean):
    def diff(conn):
        return compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)

    async with engine.connect() as conn:
        changes = await conn.run_sync(diff)
    assert changes == [], f"models and migrations differ: {changes}"


async def test_get_or_create_user_is_race_safe(sessionmaker):
    async def one():
        async with sessionmaker() as session:
            user, created = await get_or_create_user(session, 4242, username="a", language_code="en")
            await session.commit()
            return created

    results = await asyncio.gather(*[one() for _ in range(10)])
    assert results.count(True) == 1
    async with sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        user = await session.scalar(select(User))
    assert user.language == "id"  # D1: Indonesian default, regardless of client language
    assert user.language_chosen is False


async def test_state_store_roundtrip(redis_pool, clean):
    store = StateStore(redis_pool)
    assert await store.get(1) == {"step": None, "data": {}}
    await store.set(1, "img:prompt", {"tier": "fast"})
    state = await store.update(1, aspect="9:16")
    assert state == {"step": "img:prompt", "data": {"tier": "fast", "aspect": "9:16"}}
    assert 0 < await redis_pool.ttl("aimb:st:1") <= 3600
    await store.update(1, step=None)
    assert (await store.get(1))["step"] is None
    pid = await store.put_payload({"prompt": "x" * 500})
    assert len(pid) <= 10 and (await store.get_payload(pid))["prompt"] == "x" * 500
    await store.clear(1)
    assert (await store.get(1))["step"] is None


async def test_deduper(redis_pool, clean):
    dedupe = UpdateDeduper(redis_pool)
    assert await dedupe.first_seen(10) is True
    assert await dedupe.first_seen(10) is False
    await dedupe.forget(10)
    assert await dedupe.first_seen(10) is True


async def test_webhook_enqueues_into_updates_queue(stack):
    resp = await stack.post_update(updates.message(55, "/start", update_id=777))
    assert resp.json() == {"ok": True}
    assert await stack.container.redis.zcard(QUEUE_UPDATES) == 1
    again = await stack.post_update(updates.message(55, "/start", update_id=777))
    assert again.json()["duplicate"] is True
    assert await stack.container.redis.zcard(QUEUE_UPDATES) == 1


async def test_webhook_returns_503_and_forgets_update_when_queue_fails(engine, redis_pool, clean, fake_tg, settings):
    import httpx

    class BrokenRedis:
        def __getattr__(self, name):
            return getattr(redis_pool, name)

        async def enqueue_job(self, *args, **kwargs):
            raise ConnectionError("redis down")

    container = await build_container(settings, telegram=fake_tg.client(), engine=engine, redis=BrokenRedis())
    app = create_app(container)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
            resp = await http.post("/webhook/telegram", json=updates.message(1, "hi", update_id=31337),
                                   headers={"X-Telegram-Bot-Api-Secret-Token": settings.webhook_secret})
    await container.telegram.aclose()
    assert resp.status_code == 503
    assert await UpdateDeduper(redis_pool).first_seen(31337) is True  # Telegram's retry will be processed


@pytest.mark.parametrize("body", [{}, {"update_id": "x"}])
async def test_webhook_rejects_malformed_updates(stack, body):
    resp = await stack.post_update(body)
    assert resp.status_code == 400
