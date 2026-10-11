"""Credit ledger use cases (M1). Every balance change is a ledger row; reservations prevent double-spend."""

import uuid
from dataclasses import dataclass, replace
from datetime import datetime

from ..domain.billing import (
    Balance,
    Bucket,
    EntryKind,
    LedgerEntry,
    LedgerRepository,
    NewEntry,
    Reservation,
    ReservationConflict,
    ReservationStatus,
    expired_free_amount,
    split_spend,
)
from ..domain.errors import NotFound
from ..domain.ports import Clock, UnitOfWork


@dataclass(frozen=True)
class AccountSummary:
    balance: Balance
    free_expires_at: datetime | None


class LedgerService:
    def __init__(self, repo: LedgerRepository, uow: UnitOfWork, clock: Clock):
        self._repo = repo
        self._uow = uow
        self._clock = clock

    # ------------------------------------------------------------------ queries
    async def summary(self, user_id: int) -> AccountSummary:
        """Balance after applying any due free-credit expiry."""
        await self._repo.lock_account(user_id)
        await self._expire_locked(user_id)
        balance = await self._repo.balance(user_id)
        expires = await self._repo.next_free_expiry(user_id, self._clock.now()) if balance.free else None
        await self._uow.commit()
        return AccountSummary(balance, expires)

    async def history(self, user_id: int, limit: int = 20) -> list[LedgerEntry]:
        return await self._repo.history(user_id, limit)

    # ------------------------------------------------------------------ spend
    async def reserve(self, user_id: int, amount: int, *, allow_free: bool, job_id: str | None = None,
                      commit: bool = True) -> Reservation:
        """Hold credits for a job. Raises InsufficientCredits. Free credits first, and only when allowed."""
        await self._repo.lock_account(user_id)
        await self._expire_locked(user_id)
        free, paid = split_spend(await self._repo.balance(user_id), amount, allow_free)
        reservation = Reservation(id=str(uuid.uuid4()), user_id=user_id, free_amount=free, paid_amount=paid,
                                  status=ReservationStatus.HELD, job_id=job_id)
        await self._repo.create_reservation(reservation)
        await self._repo.append(self._pair(reservation, EntryKind.RESERVE, -1))
        if commit:
            await self._uow.commit()
        return reservation

    async def capture(self, reservation_id: str, *, commit: bool = True) -> Reservation:
        """The job delivered: the held credits become revenue. Idempotent."""
        reservation = await self._settle(reservation_id, ReservationStatus.CAPTURED, EntryKind.CAPTURE, 0)
        if commit:
            await self._uow.commit()
        return reservation

    async def release(self, reservation_id: str, *, commit: bool = True) -> Reservation:
        """The job failed / timed out / was blocked: credit the held amount back (auto-refund, F11). Idempotent."""
        reservation = await self._settle(reservation_id, ReservationStatus.RELEASED, EntryKind.RELEASE, 1)
        if commit:
            await self._uow.commit()
        return reservation

    async def _settle(self, reservation_id: str, target: ReservationStatus, kind: EntryKind, sign: int) -> Reservation:
        reservation = await self._repo.get_reservation(reservation_id, for_update=True)
        if reservation is None:
            raise NotFound(f"reservation {reservation_id}")
        if reservation.status == target:
            return reservation
        if reservation.status != ReservationStatus.HELD:
            raise ReservationConflict(f"reservation {reservation_id} is {reservation.status.value}")
        await self._repo.set_reservation_status(reservation_id, target, self._clock.now())
        await self._repo.append(self._pair(reservation, kind, sign))
        return replace(reservation, status=target)

    @staticmethod
    def _pair(reservation: Reservation, kind: EntryKind, sign: int) -> list[NewEntry]:
        entries = []
        for bucket, amount in ((Bucket.FREE, reservation.free_amount), (Bucket.PAID, reservation.paid_amount)):
            if amount:
                entries.append(NewEntry(user_id=reservation.user_id, bucket=bucket, amount=sign * amount, kind=kind,
                                        reservation_id=reservation.id, job_id=reservation.job_id))
        return entries

    # ------------------------------------------------------------------ credit
    async def grant(self, user_id: int, amount: int, bucket: Bucket, kind: EntryKind, *,
                    expires_at: datetime | None = None, idempotency_key: str | None = None,
                    payment_id: int | None = None, note: str | None = None, commit: bool = True) -> bool:
        """Add credits. Returns False if `idempotency_key` was already used (nothing changes)."""
        if amount <= 0:
            raise ValueError("grant amount must be positive")
        if bucket == Bucket.PAID and expires_at is not None:
            raise ValueError("paid credits never expire")
        await self._repo.lock_account(user_id)
        inserted = await self._repo.append([NewEntry(
            user_id=user_id, bucket=bucket, amount=amount, kind=kind, expires_at=expires_at,
            idempotency_key=idempotency_key, payment_id=payment_id, note=note,
        )])
        if commit:
            await self._uow.commit()
        return inserted == 1

    async def debit(self, user_id: int, amount: int, bucket: Bucket, kind: EntryKind, *,
                    idempotency_key: str | None = None, payment_id: int | None = None, note: str | None = None,
                    commit: bool = True) -> int:
        """Remove up to `amount` from a bucket without going negative (admin revoke, Stars refund).

        Returns how much was actually removed; the shortfall is the caller's to record.
        """
        if amount <= 0:
            raise ValueError("debit amount must be positive")
        await self._repo.lock_account(user_id)
        if idempotency_key and await self._repo.has_key(idempotency_key):
            return 0
        balance = await self._repo.balance(user_id)
        available = balance.free if bucket == Bucket.FREE else balance.paid
        taken = min(amount, max(0, available))
        if taken or idempotency_key:
            await self._repo.append([NewEntry(user_id=user_id, bucket=bucket, amount=-taken, kind=kind,
                                              idempotency_key=idempotency_key, payment_id=payment_id, note=note)])
        if commit:
            await self._uow.commit()
        return taken

    # ------------------------------------------------------------------ expiry
    async def expire_free(self, user_id: int) -> int:
        await self._repo.lock_account(user_id)
        expired = await self._expire_locked(user_id)
        await self._uow.commit()
        return expired

    async def expire_all(self) -> int:
        """Daily cron: expire due free credits for every account that still has some."""
        total = 0
        for user_id in await self._repo.accounts_with_free_credits():
            total += await self.expire_free(user_id)
        return total

    async def _expire_locked(self, user_id: int) -> int:
        balance = await self._repo.balance(user_id)
        if balance.free <= 0:
            return 0
        due = expired_free_amount(balance.free, await self._repo.unexpired_free_grants(user_id, self._clock.now()))
        if due:
            await self._repo.append([NewEntry(user_id=user_id, bucket=Bucket.FREE, amount=-due,
                                              kind=EntryKind.EXPIRY)])
        return due
