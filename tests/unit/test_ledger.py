from datetime import datetime, timedelta, timezone

import pytest

from app.bot.formatting import format_date, format_number, format_rupiah
from app.domain.billing import (
    Balance,
    Bucket,
    EntryKind,
    InsufficientCredits,
    ReservationConflict,
    ReservationStatus,
    expired_free_amount,
    free_allowed,
    split_spend,
)
from app.domain.errors import NotFound
from app.domain.generation import MediaKind
from app.services.ledger import LedgerService
from tests.support.fakes import FakeUnitOfWork, FixedClock, InMemoryLedgerRepository


@pytest.fixture
def ledger():
    repo, uow, clock = InMemoryLedgerRepository(), FakeUnitOfWork(), FixedClock()
    return LedgerService(repo, uow, clock), repo, uow, clock


def test_split_spend_prefers_free_and_respects_kind():
    assert split_spend(Balance(free=5, paid=10), 8, allow_free=True) == (5, 3)
    assert split_spend(Balance(free=5, paid=10), 8, allow_free=False) == (0, 8)
    with pytest.raises(InsufficientCredits) as exc:
        split_spend(Balance(free=5, paid=2), 8, allow_free=True)
    assert (exc.value.needed, exc.value.available) == (8, 7)
    with pytest.raises(InsufficientCredits) as exc:
        split_spend(Balance(free=50, paid=2), 8, allow_free=False)
    assert exc.value.available == 2
    with pytest.raises(ValueError):
        split_spend(Balance(), 0, allow_free=True)


def test_free_is_image_only_and_spendable_reflects_it():
    assert free_allowed(MediaKind.IMAGE) and not free_allowed(MediaKind.VIDEO)
    assert Balance(free=20, paid=5).spendable(MediaKind.VIDEO) == 5
    assert Balance(free=20, paid=5).spendable(MediaKind.IMAGE) == 25


@pytest.mark.parametrize("free,unexpired,expired", [(20, 0, 20), (22, 5, 17), (2, 5, 0), (0, 10, 0)])
def test_fifo_expiry_amount(free, unexpired, expired):
    assert expired_free_amount(free, unexpired) == expired


async def test_reserve_locks_account_and_writes_reserve_entries(ledger):
    svc, repo, uow, _ = ledger
    await svc.grant(1, 10, Bucket.PAID, EntryKind.PURCHASE)
    reservation = await svc.reserve(1, 4, allow_free=False, job_id="job-1")
    assert reservation.status == ReservationStatus.HELD and reservation.paid_amount == 4
    kinds = [(e.kind, e.amount, e.job_id) for _, e in repo.entries]
    assert kinds[-1] == (EntryKind.RESERVE, -4, "job-1")
    assert repo.locks.count(1) >= 2 and uow.commits == 2


async def test_capture_and_release_are_idempotent_and_exclusive(ledger):
    svc, repo, *_ = ledger
    await svc.grant(1, 10, Bucket.PAID, EntryKind.PURCHASE)
    r = await svc.reserve(1, 4, allow_free=False)
    assert (await svc.capture(r.id)).status == ReservationStatus.CAPTURED
    assert (await svc.capture(r.id)).status == ReservationStatus.CAPTURED
    with pytest.raises(ReservationConflict):
        await svc.release(r.id)
    assert (await repo.balance(1)).paid == 6
    with pytest.raises(NotFound):
        await svc.capture("missing")


async def test_commit_false_lets_callers_compose_transactions(ledger):
    svc, _, uow, _ = ledger
    await svc.grant(1, 10, Bucket.PAID, EntryKind.PURCHASE, commit=False)
    await svc.reserve(1, 2, allow_free=False, commit=False)
    assert uow.commits == 0


async def test_grant_validations_and_idempotency(ledger):
    svc, *_ = ledger
    assert await svc.grant(1, 5, Bucket.PAID, EntryKind.PURCHASE, idempotency_key="k") is True
    assert await svc.grant(1, 5, Bucket.PAID, EntryKind.PURCHASE, idempotency_key="k") is False
    with pytest.raises(ValueError):
        await svc.grant(1, 0, Bucket.PAID, EntryKind.PURCHASE)
    with pytest.raises(ValueError):
        await svc.grant(1, 5, Bucket.PAID, EntryKind.PURCHASE, expires_at=datetime.now(timezone.utc))


async def test_debit_never_goes_negative(ledger):
    svc, repo, *_ = ledger
    await svc.grant(1, 3, Bucket.FREE, EntryKind.ADMIN_GRANT)
    assert await svc.debit(1, 10, Bucket.FREE, EntryKind.ADMIN_REVOKE) == 3
    assert (await repo.balance(1)).free == 0
    assert await svc.debit(1, 10, Bucket.FREE, EntryKind.ADMIN_REVOKE) == 0
    with pytest.raises(ValueError):
        await svc.debit(1, 0, Bucket.FREE, EntryKind.ADMIN_REVOKE)


async def test_summary_reports_next_expiry_and_expire_all(ledger):
    svc, repo, _, clock = ledger
    soon, later = clock.now() + timedelta(days=3), clock.now() + timedelta(days=9)
    await svc.grant(1, 5, Bucket.FREE, EntryKind.SIGNUP_GIFT, expires_at=later)
    await svc.grant(1, 5, Bucket.FREE, EntryKind.REFERRAL_BONUS, expires_at=soon)
    await svc.grant(2, 7, Bucket.FREE, EntryKind.SIGNUP_GIFT, expires_at=soon)
    summary = await svc.summary(1)
    assert summary.balance.free == 10 and summary.free_expires_at == soon
    clock.advance(days=4)
    assert await svc.expire_all() == 12  # user 1 loses 5, user 2 loses 7
    assert (await repo.balance(1)).free == 5 and (await repo.balance(2)).free == 0
    assert [e.kind for e in await svc.history(1)][0] == EntryKind.EXPIRY


def test_locale_formatting():
    moment = datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc)  # already 12 Oct in WIB
    assert format_date(moment, "id") == "12 Okt 2026"
    assert format_date(moment, "en") == "12 Oct 2026"
    assert format_number(1300, "id") == "1.300" and format_number(1300, "en") == "1,300"
    assert format_rupiah(160000, "id") == "Rp160.000"
