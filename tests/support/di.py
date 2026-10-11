"""DI overrides for tests: swap settings and external clients, keep the production wiring for everything else."""

from typing import Any

from arq.connections import ArqRedis
from dishka import AsyncContainer, Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.di import make_container
from app.infra.telegram import TelegramClient


class TestOverrides(Provider):
    """Provides pre-built values. Nothing here is finalised by the container: the test owns their lifecycle."""

    __test__ = False  # not a pytest class

    def __init__(self, settings: Settings, telegram: TelegramClient, engine: AsyncEngine | None = None,
                 redis: ArqRedis | Any | None = None):
        super().__init__(scope=Scope.APP)
        self._settings = settings
        self._telegram = telegram
        self.provide(lambda: settings, provides=Settings)
        self.provide(lambda: telegram, provides=TelegramClient)
        if engine is not None:
            self.provide(lambda: engine, provides=AsyncEngine)
        if redis is not None:
            self.provide(lambda: redis, provides=ArqRedis)


def make_test_container(settings: Settings, telegram: TelegramClient, *extra: Provider,
                        engine: AsyncEngine | None = None, redis: Any = None) -> AsyncContainer:
    return make_container(TestOverrides(settings, telegram, engine, redis), *extra)


class ValueProvider(Provider):
    """Override any dependency type with a ready instance: ValueProvider({TaskQueue: failing_queue})."""

    def __init__(self, values: dict[Any, Any], scope: Scope = Scope.APP):
        super().__init__(scope=scope)
        for dep_type, value in values.items():
            self.provide(_constant(value), provides=dep_type)


def _constant(value: Any):
    def factory():
        return value

    return factory


__all__ = ["TestOverrides", "ValueProvider", "make_test_container", "provide"]
