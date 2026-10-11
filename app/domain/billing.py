"""Credit ledger (M1): append-only entries in two buckets, reserve → capture | release.

- `free` credits come from gifts/bonuses, expire, and can only pay for images (D5).
- `paid` credits come from Stars purchases (and referral rewards), never expire, and pay for anything.
- A reservation debits immediately (so concurrent requests can't double-spend); capture finalises it, release
  credits it back. Balances are always the sum of entries.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from .errors import DomainError
from .generation import MediaKind


class Bucket(str, Enum):
    FREE = "free"
    PAID = "paid"


class EntryKind(str, Enum):
    SIGNUP_GIFT = "signup_gift"
    PURCHASE = "purchase"
    SUBSCRIPTION = "subscription"
    RESERVE = "reserve"
    CAPTURE = "capture"          # zero-amount audit row: the held amount became revenue
    RELEASE = "release"          # held amount credited back (failure, timeout, moderation)
    ADMIN_GRANT = "admin_grant"
    ADMIN_REVOKE = "admin_revoke"
    REFERRAL_BONUS = "referral_bonus"
    REFERRAL_REWARD = "referral_reward"
    EXPIRY = "expiry"
    STARS_REFUND = "stars_refund"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"


class ReservationStatus(str, Enum):
    HELD = "held"
    CAPTURED = "captured"
    RELEASED = "released"


@dataclass(frozen=True)
class Balance:
    free: int = 0
    paid: int = 0

    @property
    def total(self) -> int:
        return self.free + self.paid

    def spendable(self, kind: MediaKind) -> int:
        return self.total if free_allowed(kind) else self.paid


@dataclass(frozen=True)
class NewEntry:
    user_id: int
    bucket: Bucket
    amount: int
    kind: EntryKind
    reservation_id: str | None = None
    job_id: str | None = None
    payment_id: int | None = None
    expires_at: datetime | None = None
    note: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class LedgerEntry:
    id: int
    user_id: int
    bucket: Bucket
    amount: int
    kind: EntryKind
    created_at: datetime
    reservation_id: str | None = None
    job_id: str | None = None
    expires_at: datetime | None = None
    note: str | None = None


@dataclass(frozen=True)
class Reservation:
    id: str
    user_id: int
    free_amount: int
    paid_amount: int
    status: ReservationStatus
    job_id: str | None = None

    @property
    def total(self) -> int:
        return self.free_amount + self.paid_amount


def free_allowed(kind: MediaKind) -> bool:
    """Free credits are image-only (D5): the cheapest video is ~35 credits, so video is gated by purchase."""
    return kind == MediaKind.IMAGE


def split_spend(balance: Balance, amount: int, allow_free: bool) -> tuple[int, int]:
    """Free credits are spent first (they expire); returns (free, paid) or raises InsufficientCredits."""
    if amount <= 0:
        raise ValueError("amount must be positive")
    free = min(balance.free, amount) if allow_free else 0
    paid = amount - free
    if paid > balance.paid:
        raise InsufficientCredits(needed=amount, available=(balance.free if allow_free else 0) + balance.paid)
    return free, paid


def expired_free_amount(free_balance: int, unexpired_grants: int) -> int:
    """FIFO expiry: spends consume the oldest grants first, so whatever exceeds the still-valid grants expired."""
    return max(0, free_balance - unexpired_grants)


class InsufficientCredits(DomainError):
    def __init__(self, needed: int, available: int):
        super().__init__(f"need {needed} credits, have {available}")
        self.needed = needed
        self.available = available


class ReservationConflict(DomainError):
    """Capture after release (or vice versa): a bug or a race the caller must handle."""


class LedgerRepository(Protocol):
    async def lock_account(self, user_id: int) -> None:
        """Serialise balance-changing operations for one account (row lock until commit)."""
        ...

    async def balance(self, user_id: int) -> Balance: ...

    async def append(self, entries: list[NewEntry]) -> int:
        """Insert entries; rows whose idempotency_key already exists are skipped. Returns rows inserted."""
        ...

    async def has_key(self, idempotency_key: str) -> bool: ...

    async def unexpired_free_grants(self, user_id: int, now: datetime) -> int:
        """Sum of positive non-release free entries whose expiry is unset or in the future."""
        ...

    async def accounts_with_free_credits(self) -> list[int]: ...

    async def create_reservation(self, reservation: Reservation) -> None: ...

    async def get_reservation(self, reservation_id: str, for_update: bool = False) -> Reservation | None: ...

    async def set_reservation_status(self, reservation_id: str, status: ReservationStatus, at: datetime) -> None: ...

    async def history(self, user_id: int, limit: int = 20) -> list[LedgerEntry]: ...

    async def next_free_expiry(self, user_id: int, now: datetime) -> datetime | None: ...
