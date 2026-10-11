"""Postgres, Alembic and Redis adapters against real services."""

import asyncio

import httpx
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select

from app.domain.errors import NotFound
from app.domain.ports import TaskQueue
from app.domain.tasks import PROCESS_UPDATE, QUEUE_UPDATES
from app.domain.users import TelegramIdentity
from app.infra.clock import SystemClock
from app.infra.db import SqlUnitOfWork
from app.infra.models import Base, UserRow
from app.infra.queue import ArqTaskQueue
from app.infra.repositories.users import SqlUserRepository
from app.infra.state import RedisUpdateDeduper, RedisWizardState
from app.main import create_app
from app.services.users import UserService
from tests.support import updates
from tests.support.di import ValueProvider, make_test_container
from tests.support.fakes import RecordingTaskQueue


async def test_migrations_match_models(engine, clean):
    def diff(conn):
        return compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)

    async with engine.connect() as conn:
        changes = await conn.run_sync(diff)
    assert changes == [], f"models and migrations differ: {changes}"


async def test_user_upsert_is_race_safe(sessionmaker):
    async def one():
        async with sessionmaker() as session:
            service = UserService(SqlUserRepository(session, SystemClock()), SqlUnitOfWork(session))
            _, created = await service.register_seen(TelegramIdentity(4242, "a", "en"))
            return created

    results = await asyncio.gather(*[one() for _ in range(10)])
    assert results.count(True) == 1
    async with sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(UserRow)) == 1
        row = await session.scalar(select(UserRow))
    assert row.language == "id"  # D1: Indonesian default, regardless of client language
    assert row.language_chosen is False


async def test_user_repository_updates_and_maps_to_domain(sessionmaker):
    async with sessionmaker() as session:
        repo = SqlUserRepository(session, SystemClock())
        user, created = await repo.upsert_seen(TelegramIdentity(77, "sari"))
        assert created and user.username == "sari"
        user = await repo.set_language(user.id, "en")
        assert (user.language, user.language_chosen) == ("en", True)
        user = await repo.set_banned(user.id, True, "spam")
        assert user.is_banned and user.ban_reason == "spam"
        await session.commit()
        again, created = await repo.upsert_seen(TelegramIdentity(77, None))
        assert not created and again.username is None and again.language == "en"
        assert await repo.get(again.id) == again
        with pytest.raises(NotFound):
            await repo.set_language(999999, "id")


async def test_wizard_state_roundtrip(redis_pool, clean):
    store = RedisWizardState(redis_pool)
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
    dedupe = RedisUpdateDeduper(redis_pool)
    assert await dedupe.seen(10) is False
    await dedupe.mark(10)
    assert await dedupe.seen(10) is True
    assert 0 < await redis_pool.ttl("aimb:upd:10") <= 48 * 3600


async def test_arq_task_queue_is_idempotent_by_job_id(redis_pool, clean):
    queue = ArqTaskQueue(redis_pool)
    assert await queue.enqueue(PROCESS_UPDATE, {"update_id": 1}, queue=QUEUE_UPDATES, job_id="upd:1") is True
    assert await queue.enqueue(PROCESS_UPDATE, {"update_id": 1}, queue=QUEUE_UPDATES, job_id="upd:1") is False
    assert await redis_pool.zcard(QUEUE_UPDATES) == 1


async def test_webhook_enqueues_into_updates_queue(stack):
    resp = await stack.post_update(updates.message(55, "/start", update_id=777))
    assert resp.json() == {"ok": True}
    assert await stack.redis.zcard(QUEUE_UPDATES) == 1
    again = await stack.post_update(updates.message(55, "/start", update_id=777))
    assert again.json()["duplicate"] is True
    assert await stack.redis.zcard(QUEUE_UPDATES) == 1


async def test_webhook_returns_503_and_forgets_update_when_queue_fails(engine, redis_pool, clean, fake_tg, settings):
    telegram = fake_tg.client()
    di = make_test_container(settings, telegram, ValueProvider({TaskQueue: RecordingTaskQueue(fail=True)}),
                             engine=engine, redis=redis_pool)
    app = create_app(di)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
            resp = await http.post("/webhook/telegram", json=updates.message(1, "hi", update_id=31337),
                                   headers={"X-Telegram-Bot-Api-Secret-Token": settings.webhook_secret})
    await di.close()
    await telegram.aclose()
    assert resp.status_code == 503
    assert await RedisUpdateDeduper(redis_pool).seen(31337) is False  # Telegram's retry will be processed


@pytest.mark.parametrize("body", [{}, {"update_id": "x"}, {"update_id": True}])
async def test_webhook_rejects_malformed_updates(stack, body):
    resp = await stack.post_update(body)
    assert resp.status_code == 400
