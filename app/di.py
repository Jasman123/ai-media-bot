"""Composition root: the one place that binds ports to concrete adapters (dishka).

Scopes:
- APP: process singletons (settings, engine, Redis pool, Telegram client, router, stateless adapters).
- REQUEST: one per HTTP request or ARQ job (DB session, unit of work, repositories, services, dispatcher).

Tests pass extra providers to `make_container(...)`; later providers override earlier ones.
"""

from collections.abc import AsyncIterator

from arq.connections import ArqRedis
from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .bot import build_router
from .bot.dispatcher import BotConfig, Dispatcher
from .bot.router import Router
from .config import Settings, get_settings
from .domain.ports import Clock, TaskQueue, UnitOfWork, UpdateDeduper, WizardState
from .domain.users import UserRepository
from .i18n import I18n
from .infra.clock import SystemClock
from .infra.db import SqlUnitOfWork, make_engine, make_sessionmaker
from .infra.queue import ArqTaskQueue, create_redis
from .infra.repositories.users import SqlUserRepository
from .infra.state import RedisUpdateDeduper, RedisWizardState
from .infra.telegram import TelegramClient
from .services.updates import UpdateIntake
from .services.users import UserService


class ConfigProvider(Provider):
    @provide(scope=Scope.APP)
    def settings(self) -> Settings:
        return get_settings()


class InfraProvider(Provider):
    scope = Scope.APP

    @provide
    async def engine(self, settings: Settings) -> AsyncIterator[AsyncEngine]:
        engine = make_engine(settings.database_url, pool_size=settings.db_pool_size)
        yield engine
        await engine.dispose()

    @provide
    def sessionmaker(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return make_sessionmaker(engine)

    @provide
    async def redis(self, settings: Settings) -> AsyncIterator[ArqRedis]:
        redis = await create_redis(settings.redis_url)
        yield redis
        await redis.aclose()

    @provide
    async def telegram(self, settings: Settings) -> AsyncIterator[TelegramClient]:
        client = TelegramClient(settings.bot_token, settings.telegram_api_base, settings.telegram_timeout_s)
        yield client
        await client.aclose()

    @provide
    def i18n(self) -> I18n:
        return I18n.load()

    clock = provide(SystemClock, provides=Clock)
    task_queue = provide(ArqTaskQueue, provides=TaskQueue)

    @provide
    def wizard_state(self, redis: ArqRedis) -> WizardState:
        return RedisWizardState(redis)

    @provide
    def deduper(self, redis: ArqRedis) -> UpdateDeduper:
        return RedisUpdateDeduper(redis)

    @provide(scope=Scope.REQUEST)
    async def session(self, sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    uow = provide(SqlUnitOfWork, scope=Scope.REQUEST, provides=UnitOfWork)


class RepositoryProvider(Provider):
    scope = Scope.REQUEST

    users = provide(SqlUserRepository, provides=UserRepository)


class ServiceProvider(Provider):
    users = provide(UserService, scope=Scope.REQUEST)
    update_intake = provide(UpdateIntake, scope=Scope.APP)


class BotProvider(Provider):
    @provide(scope=Scope.APP)
    def router(self) -> Router:
        return build_router()

    @provide(scope=Scope.APP)
    def bot_config(self, settings: Settings) -> BotConfig:
        return BotConfig(username=settings.bot_username)

    dispatcher = provide(Dispatcher, scope=Scope.REQUEST)


def providers() -> list[Provider]:
    return [ConfigProvider(), InfraProvider(), RepositoryProvider(), ServiceProvider(), BotProvider()]


def make_container(*overrides: Provider) -> AsyncContainer:
    return make_async_container(*providers(), *overrides)


async def warm_up(container: AsyncContainer) -> None:
    """Resolve the APP-scoped infrastructure up front so a missing Postgres/Redis fails startup, not a request."""
    await container.get(AsyncEngine)
    await container.get(ArqRedis)
    await container.get(TelegramClient)
    await container.get(UpdateIntake)
