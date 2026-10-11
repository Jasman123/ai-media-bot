from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class User(Base):
    """A Telegram account (kind='user') or a group chat holding a shared credit pool (kind='group').

    PDP minimisation: we keep the Telegram id, an optional username for support, and the language. No names,
    no phone numbers.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(8), default="user", server_default="user")
    username: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(5), default="id", server_default="id")
    language_chosen: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    ban_reason: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
