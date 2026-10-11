from datetime import datetime

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.billing import (
    Balance,
    Bucket,
    EntryKind,
    LedgerEntry,
    NewEntry,
    Reservation,
    ReservationStatus,
)
from ..models import LedgerEntryRow, ReservationRow, UserRow

GRANT_KINDS_EXCLUDED_FROM_EXPIRY = (EntryKind.RELEASE.value,)


class SqlLedgerRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def lock_account(self, user_id: int) -> None:
        await self._session.execute(select(UserRow.id).where(UserRow.id == user_id).with_for_update())

    async def balance(self, user_id: int) -> Balance:
        row = (await self._session.execute(
            select(
                func.coalesce(func.sum(case((LedgerEntryRow.bucket == Bucket.FREE.value, LedgerEntryRow.amount),
                                            else_=0)), 0),
                func.coalesce(func.sum(case((LedgerEntryRow.bucket == Bucket.PAID.value, LedgerEntryRow.amount),
                                            else_=0)), 0),
            ).where(LedgerEntryRow.user_id == user_id)
        )).one()
        return Balance(free=int(row[0]), paid=int(row[1]))

    async def append(self, entries: list[NewEntry]) -> int:
        if not entries:
            return 0
        values = [dict(user_id=e.user_id, bucket=e.bucket.value, amount=e.amount, kind=e.kind.value,
                       reservation_id=e.reservation_id, job_id=e.job_id, payment_id=e.payment_id,
                       expires_at=e.expires_at, note=e.note, idempotency_key=e.idempotency_key) for e in entries]
        result = await self._session.execute(
            insert(LedgerEntryRow).values(values)
            .on_conflict_do_nothing(index_elements=[LedgerEntryRow.idempotency_key])
            .returning(LedgerEntryRow.id)
        )
        return len(result.all())

    async def has_key(self, idempotency_key: str) -> bool:
        return await self._session.scalar(
            select(func.count()).where(LedgerEntryRow.idempotency_key == idempotency_key)
        ) > 0

    async def unexpired_free_grants(self, user_id: int, now: datetime) -> int:
        return int(await self._session.scalar(
            select(func.coalesce(func.sum(LedgerEntryRow.amount), 0)).where(
                LedgerEntryRow.user_id == user_id,
                LedgerEntryRow.bucket == Bucket.FREE.value,
                LedgerEntryRow.amount > 0,
                LedgerEntryRow.kind.notin_(GRANT_KINDS_EXCLUDED_FROM_EXPIRY),
                or_(LedgerEntryRow.expires_at.is_(None), LedgerEntryRow.expires_at > now),
            )
        ))

    async def next_free_expiry(self, user_id: int, now: datetime) -> datetime | None:
        return await self._session.scalar(
            select(func.min(LedgerEntryRow.expires_at)).where(
                LedgerEntryRow.user_id == user_id,
                LedgerEntryRow.bucket == Bucket.FREE.value,
                LedgerEntryRow.amount > 0,
                LedgerEntryRow.expires_at > now,
            )
        )

    async def accounts_with_free_credits(self) -> list[int]:
        rows = await self._session.scalars(
            select(LedgerEntryRow.user_id).where(LedgerEntryRow.bucket == Bucket.FREE.value)
            .group_by(LedgerEntryRow.user_id).having(func.sum(LedgerEntryRow.amount) > 0)
        )
        return list(rows)

    async def create_reservation(self, reservation: Reservation) -> None:
        self._session.add(ReservationRow(
            id=reservation.id, user_id=reservation.user_id, free_amount=reservation.free_amount,
            paid_amount=reservation.paid_amount, status=reservation.status.value, job_id=reservation.job_id,
        ))
        await self._session.flush()

    async def get_reservation(self, reservation_id: str, for_update: bool = False) -> Reservation | None:
        stmt = select(ReservationRow).where(ReservationRow.id == reservation_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = await self._session.scalar(stmt.execution_options(populate_existing=True))
        if row is None:
            return None
        return Reservation(id=row.id, user_id=row.user_id, free_amount=row.free_amount, paid_amount=row.paid_amount,
                           status=ReservationStatus(row.status), job_id=row.job_id)

    async def set_reservation_status(self, reservation_id: str, status: ReservationStatus, at: datetime) -> None:
        await self._session.execute(
            update(ReservationRow).where(ReservationRow.id == reservation_id)
            .values(status=status.value, settled_at=at)
        )

    async def history(self, user_id: int, limit: int = 20) -> list[LedgerEntry]:
        rows = await self._session.scalars(
            select(LedgerEntryRow)
            .where(LedgerEntryRow.user_id == user_id, and_(LedgerEntryRow.amount != 0))
            .order_by(LedgerEntryRow.id.desc()).limit(limit)
        )
        return [LedgerEntry(id=r.id, user_id=r.user_id, bucket=Bucket(r.bucket), amount=r.amount,
                            kind=EntryKind(r.kind), created_at=r.created_at, reservation_id=r.reservation_id,
                            job_id=r.job_id, expires_at=r.expires_at, note=r.note) for r in rows]
