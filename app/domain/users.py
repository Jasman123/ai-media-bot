from dataclasses import dataclass
from typing import Protocol

DEFAULT_LANGUAGE = "id"  # D1: Indonesia only, Bahasa Indonesia first
SUPPORTED_LANGUAGES = ("id", "en")


@dataclass(frozen=True)
class User:
    id: int
    telegram_id: int
    kind: str = "user"  # "user" or "group" (a group chat's shared credit pool)
    username: str | None = None
    language: str = DEFAULT_LANGUAGE
    language_chosen: bool = False
    is_banned: bool = False
    ban_reason: str | None = None


@dataclass(frozen=True)
class TelegramIdentity:
    """Who sent an update, as far as the core cares. PDP-minimal: no names, no phone numbers."""

    telegram_id: int
    username: str | None = None
    language_code: str | None = None
    kind: str = "user"


def normalize_language(code: str | None) -> str:
    return code if code in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


class UserRepository(Protocol):
    async def upsert_seen(self, identity: TelegramIdentity) -> tuple[User, bool]: ...

    async def get(self, user_id: int) -> User | None: ...

    async def get_by_telegram_id(self, telegram_id: int) -> User | None: ...

    async def set_language(self, user_id: int, language: str) -> User: ...

    async def set_banned(self, user_id: int, banned: bool, reason: str | None) -> User: ...
