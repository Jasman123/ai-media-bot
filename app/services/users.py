from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import utcnow
from ..models import User


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None = None,
    language_code: str | None = None,
    kind: str = "user",
) -> tuple[User, bool]:
    """Race-safe upsert keyed on telegram_id. Returns (user, created).

    New users start in Bahasa Indonesia (D1) whatever their Telegram client language; /start offers a picker.
    """
    inserted = await session.scalar(
        insert(User)
        .values(telegram_id=telegram_id, username=username, kind=kind, language="id")
        .on_conflict_do_nothing(index_elements=[User.telegram_id])
        .returning(User.id)
    )
    if inserted is None:
        await session.execute(
            update(User).where(User.telegram_id == telegram_id).values(last_seen_at=utcnow(), username=username)
        )
    user = await session.scalar(
        select(User).where(User.telegram_id == telegram_id).execution_options(populate_existing=True)
    )
    return user, inserted is not None


async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> User | None:
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def set_language(session: AsyncSession, user: User, language: str) -> None:
    user.language = language
    user.language_chosen = True
    session.add(user)
