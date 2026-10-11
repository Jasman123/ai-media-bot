"""Runs tests/fixtures/ledger/scenarios.json against any LedgerService factory (in-memory or Postgres)."""

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from pathlib import Path

from app.domain.billing import Bucket, EntryKind, free_allowed
from app.domain.errors import DomainError
from app.domain.generation import MediaKind
from app.services.ledger import LedgerService
from tests.support.fakes import FixedClock

SCENARIOS = json.loads((Path(__file__).parent.parent / "fixtures" / "ledger" / "scenarios.json").read_text())[
    "scenarios"]

ServiceFactory = Callable[[], AbstractAsyncContextManager[LedgerService]]


async def run_scenario(scenario: dict, service_factory: ServiceFactory, clock: FixedClock, user_id: int) -> None:
    refs = {}
    for index, step in enumerate(scenario["steps"]):
        where = f"{scenario['name']} / step {index} ({step['op']})"
        async with service_factory() as svc:
            op = step["op"]
            try:
                if op == "grant":
                    days = step.get("expires_in_days")
                    await svc.grant(user_id, step["amount"], Bucket(step["bucket"]), EntryKind(step["kind"]),
                                    expires_at=clock.now() + timedelta(days=days) if days else None,
                                    idempotency_key=step.get("key"))
                elif op == "reserve":
                    reservation = await svc.reserve(user_id, step["amount"],
                                                    allow_free=free_allowed(MediaKind(step["kind"])))
                    if step.get("as"):
                        refs[step["as"]] = reservation
                elif op == "capture":
                    await svc.capture(refs[step["ref"]].id)
                elif op == "release":
                    await svc.release(refs[step["ref"]].id)
                elif op == "debit":
                    taken = await svc.debit(user_id, step["amount"], Bucket(step["bucket"]), EntryKind(step["kind"]),
                                            idempotency_key=step.get("key"))
                    assert taken == step["taken"], f"{where}: took {taken}"
                elif op == "expire":
                    await svc.expire_free(user_id)
                elif op == "advance_days":
                    clock.advance(days=step["days"])
                else:
                    raise AssertionError(f"unknown op {op}")
            except DomainError as exc:
                assert step.get("error") == type(exc).__name__, f"{where}: unexpected {exc!r}"
            else:
                assert "error" not in step, f"{where}: expected {step['error']}"
        if "expect" in step:
            async with service_factory() as svc:
                balance = (await svc.summary(user_id)).balance
            assert {"free": balance.free, "paid": balance.paid} == step["expect"], where
