"""Ledger against Postgres: append-only trigger, row-lock concurrency, idempotency, the shared scenarios."""

import asyncio
import contextlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.billing import Bucket, EntryKind, InsufficientCredits
from app.domain.users import TelegramIdentity
from app.infra.clock import SystemClock
from app.infra.db import SqlUnitOfWork
from app.infra.repositories.ledger import SqlLedgerRepository
from app.infra.repositories.users import SqlUserRepository
from app.services.ledger import LedgerService
from tests.support.fakes import FixedClock
from tests.support.ledger_scenarios import SCENARIOS, run_scenario


async def make_user(sessionmaker, telegram_id: int) -> int:
    async with sessionmaker() as session:
        user, _ = await SqlUserRepository(session, SystemClock()).upsert_seen(TelegramIdentity(telegram_id))
        await session.commit()
        return user.id


def factory(sessionmaker, clock):
    @contextlib.asynccontextmanager
    async def make():
        async with sessionmaker() as session:
            yield LedgerService(SqlLedgerRepository(session), SqlUnitOfWork(session), clock)

    return make


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
async def test_scenario_on_postgres(sessionmaker, scenario):
    user_id = await make_user(sessionmaker, 5001)
    clock = FixedClock()
    await run_scenario(scenario, factory(sessionmaker, clock), clock, user_id)


async def test_ledger_rows_cannot_be_updated_or_deleted(sessionmaker):
    user_id = await make_user(sessionmaker, 5002)
    async with factory(sessionmaker, SystemClock())() as svc:
        await svc.grant(user_id, 10, Bucket.PAID, EntryKind.PURCHASE)
    for statement in ("UPDATE ledger_entries SET amount = 1000", "DELETE FROM ledger_entries"):
        async with sessionmaker() as session:
            with pytest.raises(DBAPIError, match="append-only"):
                await session.execute(text(statement))


async def test_concurrent_reservations_never_overspend(sessionmaker):
    user_id = await make_user(sessionmaker, 5003)
    make = factory(sessionmaker, SystemClock())
    async with make() as svc:
        await svc.grant(user_id, 30, Bucket.PAID, EntryKind.PURCHASE)

    async def attempt():
        async with make() as svc:
            try:
                await svc.reserve(user_id, 3, allow_free=False)
                return True
            except InsufficientCredits:
                return False

    results = await asyncio.gather(*[attempt() for _ in range(20)])
    assert results.count(True) == 10
    async with make() as svc:
        assert (await svc.summary(user_id)).balance.paid == 0


async def test_concurrent_duplicate_purchase_grants_credit_once(sessionmaker):
    user_id = await make_user(sessionmaker, 5004)
    make = factory(sessionmaker, SystemClock())

    async def grant():
        async with make() as svc:
            return await svc.grant(user_id, 165, Bucket.PAID, EntryKind.PURCHASE, idempotency_key="pay:dup")

    results = await asyncio.gather(*[grant() for _ in range(8)])
    assert results.count(True) == 1
    async with make() as svc:
        assert (await svc.summary(user_id)).balance.paid == 165


async def test_history_hides_zero_amount_audit_rows(sessionmaker):
    user_id = await make_user(sessionmaker, 5005)
    make = factory(sessionmaker, SystemClock())
    async with make() as svc:
        await svc.grant(user_id, 10, Bucket.PAID, EntryKind.PURCHASE)
        reservation = await svc.reserve(user_id, 4, allow_free=False, job_id="j-1")
        await svc.capture(reservation.id)
        history = await svc.history(user_id)
    assert [(e.kind, e.amount) for e in history] == [(EntryKind.RESERVE, -4), (EntryKind.PURCHASE, 10)]
