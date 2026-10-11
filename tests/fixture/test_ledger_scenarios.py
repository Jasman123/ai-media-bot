"""Ledger scenarios from tests/fixtures/ledger/scenarios.json against the in-memory repository."""

import contextlib

import pytest

from app.services.ledger import LedgerService
from tests.support.fakes import FakeUnitOfWork, FixedClock, InMemoryLedgerRepository
from tests.support.ledger_scenarios import SCENARIOS, run_scenario


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
async def test_scenario_in_memory(scenario):
    clock = FixedClock()
    service = LedgerService(InMemoryLedgerRepository(), FakeUnitOfWork(), clock)

    @contextlib.asynccontextmanager
    async def factory():
        yield service

    await run_scenario(scenario, factory, clock, user_id=1)
