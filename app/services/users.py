from ..domain.errors import NotFound
from ..domain.ports import UnitOfWork
from ..domain.users import TelegramIdentity, User, UserRepository, normalize_language


class UserService:
    def __init__(self, users: UserRepository, uow: UnitOfWork):
        self._users = users
        self._uow = uow

    async def register_seen(self, identity: TelegramIdentity) -> tuple[User, bool]:
        """Create the user on first contact (race-safe) or refresh last-seen. Returns (user, created)."""
        user, created = await self._users.upsert_seen(identity)
        await self._uow.commit()
        return user, created

    async def choose_language(self, user_id: int, language: str) -> User:
        user = await self._users.set_language(user_id, normalize_language(language))
        await self._uow.commit()
        return user

    async def get_by_telegram_id(self, telegram_id: int) -> User:
        user = await self._users.get_by_telegram_id(telegram_id)
        if user is None:
            raise NotFound(f"user {telegram_id}")
        return user

    async def set_banned(self, telegram_id: int, banned: bool, reason: str | None = None) -> User:
        user = await self.get_by_telegram_id(telegram_id)
        user = await self._users.set_banned(user.id, banned, reason if banned else None)
        await self._uow.commit()
        return user
