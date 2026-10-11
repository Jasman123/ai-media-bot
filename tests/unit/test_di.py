"""The composition root: graph validity, scopes, and that every handler dependency is resolvable."""

from dishka import Scope
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.dispatcher import Dispatcher
from app.bot.router import Router
from app.di import make_container, providers
from app.domain.ports import Clock, TaskQueue, UnitOfWork, UpdateDeduper, WizardState
from app.domain.users import UserRepository
from app.infra.clock import SystemClock
from app.infra.db import SqlUnitOfWork, make_engine
from app.infra.queue import ArqTaskQueue
from app.infra.repositories.users import SqlUserRepository
from app.infra.state import RedisUpdateDeduper, RedisWizardState
from app.services.updates import UpdateIntake
from app.services.users import UserService
from tests.conftest import TEST_DATABASE_URL, make_settings
from tests.support.di import ValueProvider, make_test_container
from tests.support.fake_telegram import FakeTelegram
from tests.support.fakes import FixedClock


def test_production_graph_validates_without_env():
    container = make_container()  # validation happens at build time; nothing is resolved
    assert container is not None


async def test_ports_are_bound_to_infra_adapters():
    telegram = FakeTelegram().client()
    engine = make_engine(TEST_DATABASE_URL)  # lazy: no connection is opened by resolving the graph
    di = make_test_container(make_settings(), telegram, engine=engine, redis=object())
    try:
        assert isinstance(await di.get(Clock), SystemClock)
        assert isinstance(await di.get(TaskQueue), ArqTaskQueue)
        assert isinstance(await di.get(WizardState), RedisWizardState)
        assert isinstance(await di.get(UpdateDeduper), RedisUpdateDeduper)
        assert isinstance(await di.get(UpdateIntake), UpdateIntake)
        assert await di.get(Router) is await di.get(Router)  # APP scope: singleton
        async with di() as request:
            assert isinstance(await request.get(UnitOfWork), SqlUnitOfWork)
            assert isinstance(await request.get(UserRepository), SqlUserRepository)
            service = await request.get(UserService)
            assert service is await request.get(UserService)  # one per request
            assert isinstance(await request.get(Dispatcher), Dispatcher)
        async with di() as other_request:
            assert await other_request.get(UserService) is not service  # fresh per request/job
    finally:
        await di.close()
        await engine.dispose()
        await telegram.aclose()


async def test_every_handler_dependency_resolves_in_request_scope():
    telegram = FakeTelegram().client()
    engine = make_engine(TEST_DATABASE_URL)
    di = make_test_container(make_settings(), telegram, engine=engine, redis=object())
    try:
        router = await di.get(Router)
        async with di() as request:
            for dep_type in router.dependency_types():
                assert await request.get(dep_type) is not None, dep_type
    finally:
        await di.close()
        await engine.dispose()
        await telegram.aclose()


async def test_overrides_replace_production_bindings():
    telegram = FakeTelegram().client()
    clock = FixedClock()
    di = make_test_container(make_settings(), telegram, ValueProvider({Clock: clock}), redis=object())
    try:
        assert await di.get(Clock) is clock
    finally:
        await di.close()
        await telegram.aclose()


def test_providers_use_only_app_and_request_scopes_and_sessions_are_per_request():
    scopes = {f.provides.type_hint: f.scope for p in providers() for f in p.factories}
    assert set(scopes.values()) == {Scope.APP, Scope.REQUEST}
    # Anything holding a DB session must be per request/job, never a process singleton.
    for dep in (AsyncSession, UnitOfWork, UserRepository, UserService, Dispatcher):
        assert scopes[dep] == Scope.REQUEST, dep
