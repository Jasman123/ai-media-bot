import os
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.container import build_container
from app.main import create_app
from tests.support.fake_telegram import TEST_TOKEN, FakeTelegram

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
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def fake_tg() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
async def container(settings, fake_tg):
    ctx = build_container(settings, telegram=fake_tg.client())
    yield ctx
    await ctx.aclose()


@pytest.fixture
async def api(container):
    """HTTP client against the ASGI app with its lifespan running."""
    app = create_app(container)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client


def load_fixture(*parts: str):
    import json

    return json.loads((FIXTURES.joinpath(*parts)).read_text())
