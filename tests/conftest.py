import asyncio
import json
import os
import uuid
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.container import build_container
from app.main import create_app
from tests.support.fake_telegram import TEST_TOKEN, FakeTelegram

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://aimb:aimb@127.0.0.1:55432/aimb")
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:56379/0")
WEBHOOK_SECRET = "test-webhook-secret"
API_KEY = "test-api-key"


def pytest_collection_modifyitems(config, items):
    """Tag every test with its layer marker from its directory, so `-m unit` etc. work."""
    for item in items:
        parts = Path(str(item.fspath)).parts
        for layer in ("unit", "fixture", "integration", "e2e"):
            if layer in parts:
                item.add_marker(getattr(pytest.mark, layer))


def make_settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        env="test",
        bot_token=TEST_TOKEN,
        bot_username="test_media_bot",
        webhook_url="",
        webhook_secret=WEBHOOK_SECRET,
        api_key=API_KEY,
        database_url=TEST_DATABASE_URL,
        redis_url=TEST_REDIS_URL,
    )
    base.update(overrides)
    return Settings(**base)


def load_fixture(*parts: str):
    return json.loads((FIXTURES.joinpath(*parts)).read_text())


# ---------------------------------------------------------------------------- light fixtures (no services)
@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def fake_tg() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
async def container(settings, fake_tg):
    """Container without Postgres/Redis, for unit tests."""
    ctx = await build_container(settings, telegram=fake_tg.client(), engine=None, redis=None)
    yield ctx
    await ctx.telegram.aclose()


# ---------------------------------------------------------------------------- services (integration / e2e)
def run_migrations(url: str, revision: str = "head") -> None:
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, revision)


@pytest.fixture(scope="session")
async def pg_url():
    """A fresh database for this test session, migrated with Alembic (so migrations are tested too)."""
    import asyncpg

    admin_dsn = TEST_DATABASE_URL.replace("+asyncpg", "")
    name = f"aimb_test_{uuid.uuid4().hex[:10]}"
    conn = await asyncpg.connect(admin_dsn)
    await conn.execute(f'CREATE DATABASE "{name}"')
    await conn.close()
    url = TEST_DATABASE_URL.rsplit("/", 1)[0] + f"/{name}"
    await asyncio.to_thread(run_migrations, url)
    yield url
    conn = await asyncpg.connect(admin_dsn)
    await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    await conn.close()


@pytest.fixture(scope="session")
async def engine(pg_url):
    from app.db import make_engine

    eng = make_engine(pg_url, pool_size=5)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
async def redis_pool():
    from app.queue import create_redis

    pool = await create_redis(TEST_REDIS_URL)
    yield pool
    await pool.aclose()


@pytest.fixture
async def clean(engine, redis_pool):
    """Empty every table and the Redis db before a test."""
    from sqlalchemy import text

    from app.models import Base

    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await redis_pool.flushdb()
    yield


@pytest.fixture
def sessionmaker(engine, clean):
    from app.db import make_sessionmaker

    return make_sessionmaker(engine)


@pytest.fixture
async def stack(engine, redis_pool, clean, fake_tg, settings):
    from tests.support.stack import Stack

    container = await build_container(settings, telegram=fake_tg.client(), engine=engine, redis=redis_pool)
    app = create_app(container)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            yield Stack(container, fake_tg, http)
    await container.telegram.aclose()


@pytest.fixture
async def api(stack):
    return stack.http
