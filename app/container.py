"""Process-wide dependencies, built once and shared by the API and the worker.

Tests build a Container with fakes instead of patching module globals. Anything passed in via overrides is
owned by the caller and is not closed by `aclose()`.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from arq.connections import ArqRedis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .config import Settings
from .db import make_engine, make_sessionmaker
from .i18n import I18n
from .queue import create_redis
from .state import StateStore, UpdateDeduper
from .telegram import TelegramClient

if TYPE_CHECKING:
    from .bot.router import Router


@dataclass
class Container:
    settings: Settings
    telegram: TelegramClient
    i18n: I18n
    engine: AsyncEngine | None = None
    db: async_sessionmaker[AsyncSession] | None = None
    redis: ArqRedis | None = None
    state: StateStore | None = None
    deduper: UpdateDeduper | None = None
    router: "Router | None" = None
    owned: set[str] = field(default_factory=set)

    async def aclose(self) -> None:
        if "telegram" in self.owned:
            await self.telegram.aclose()
        if "redis" in self.owned and self.redis is not None:
            await self.redis.aclose()
        if "engine" in self.owned and self.engine is not None:
            await self.engine.dispose()


async def build_container(settings: Settings, **overrides: Any) -> Container:
    owned: set[str] = set()

    telegram = overrides.pop("telegram", None)
    if telegram is None:
        telegram = TelegramClient(settings.bot_token, settings.telegram_api_base, settings.telegram_timeout_s)
        owned.add("telegram")

    if "engine" in overrides:
        engine = overrides.pop("engine")
    else:
        engine = make_engine(settings.database_url, pool_size=settings.db_pool_size)
        owned.add("engine")

    if "redis" in overrides:
        redis = overrides.pop("redis")
    else:
        redis = await create_redis(settings.redis_url)
        owned.add("redis")

    router = overrides.pop("router", None)
    if router is None:
        from .bot import build_router

        router = build_router()

    return Container(
        settings=settings,
        telegram=telegram,
        i18n=overrides.pop("i18n", None) or I18n.load(),
        engine=engine,
        db=make_sessionmaker(engine) if engine is not None else None,
        redis=redis,
        state=StateStore(redis) if redis is not None else None,
        deduper=UpdateDeduper(redis) if redis is not None else None,
        router=router,
        owned=owned,
        **overrides,
    )
