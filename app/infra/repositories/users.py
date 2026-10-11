from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.errors import NotFound
from ...domain.ports import Clock
from ...domain.users import DEFAULT_LANGUAGE, TelegramIdentity, User
from ..models import UserRow


def to_user(row: UserRow) -> User:
    return User(
        id=row.id,
        telegram_id=row.telegram_id,
        kind=row.kind,
        username=row.username,
        language=row.language,
        language_chosen=row.language_chosen,
        is_banned=row.is_banned,
        ban_reason=row.ban_reason,
    )


class SqlUserRepository:
    def __init__(self, session: AsyncSession, clock: Clock):
        self._session = session
        self._clock = clock

    async def upsert_seen(self, identity: TelegramIdentity) -> tuple[User, bool]:
        # New users start in Bahasa Indonesia (D1) whatever their client language; /start offers a picker.
        inserted = await self._session.scalar(
            insert(UserRow)
            .values(telegram_id=identity.telegram_id, username=identity.username, kind=identity.kind,
                    language=DEFAULT_LANGUAGE)
            .on_conflict_do_nothing(index_elements=[UserRow.telegram_id])
            .returning(UserRow.id)
        )
        if inserted is None:
            await self._session.execute(
                update(UserRow)
                .where(UserRow.telegram_id == identity.telegram_id)
                .values(last_seen_at=self._clock.now(), username=identity.username)
            )
        row = await self._row_by_telegram_id(identity.telegram_id)
        return to_user(row), inserted is not None

    async def get(self, user_id: int) -> User | None:
        row = await self._session.get(UserRow, user_id, populate_existing=True)
        return to_user(row) if row else None

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        row = await self._row_by_telegram_id(telegram_id)
        return to_user(row) if row else None

    async def set_language(self, user_id: int, language: str) -> User:
        return await self._update(user_id, language=language, language_chosen=True)

    async def set_banned(self, user_id: int, banned: bool, reason: str | None) -> User:
        return await self._update(user_id, is_banned=banned, ban_reason=reason)

    async def _row_by_telegram_id(self, telegram_id: int) -> UserRow | None:
        return await self._session.scalar(
            select(UserRow).where(UserRow.telegram_id == telegram_id).execution_options(populate_existing=True)
        )

    async def _update(self, user_id: int, **values) -> User:
        row = await self._session.scalar(
            update(UserRow).where(UserRow.id == user_id).values(**values).returning(UserRow)
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFound(f"user id {user_id}")
        return to_user(row)
