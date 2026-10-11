from dataclasses import dataclass, field
from typing import Any

from ..domain.ports import WizardState
from ..domain.users import User
from ..i18n import I18n
from ..infra.telegram import TelegramClient, TelegramError


@dataclass
class Ctx:
    """Presentation context for one update: who, where, and how to talk back. No business logic here."""

    update: dict
    user: User
    tg: TelegramClient
    i18n: I18n
    state: WizardState
    chat_id: int | None = None
    chat_type: str = "private"
    message: dict | None = None
    callback: dict | None = None
    args: list[str] = field(default_factory=list)

    @property
    def lang(self) -> str:
        return self.user.language

    @property
    def is_private(self) -> bool:
        return self.chat_type == "private"

    @property
    def text(self) -> str:
        if not self.message:
            return ""
        return self.message.get("text") or self.message.get("caption") or ""

    @property
    def callback_message_id(self) -> int | None:
        if self.callback and self.callback.get("message"):
            return self.callback["message"]["message_id"]
        return None

    def t(self, key: str, **kwargs: Any) -> str:
        return self.i18n.t(self.lang, key, **kwargs)

    async def reply(self, text: str, reply_markup: dict | None = None, **kwargs: Any) -> dict:
        return await self.tg.send_message(self.chat_id, text, reply_markup=reply_markup, **kwargs)

    async def edit_or_reply(self, text: str, reply_markup: dict | None = None) -> Any:
        """On a button press, replace the pressed message; otherwise send a new one."""
        message_id = self.callback_message_id
        if message_id is not None:
            try:
                return await self.tg.edit_message_text(self.chat_id, message_id, text, reply_markup=reply_markup)
            except TelegramError:
                pass  # e.g. the message was a photo or is too old to edit
        return await self.reply(text, reply_markup)

    async def answer(self, text: str | None = None, alert: bool = False) -> None:
        if self.callback:
            await self.tg.answer_callback_query(self.callback["id"], text=text, show_alert=alert)

    def with_user(self, user: User) -> "Ctx":
        """Swap in a refreshed user (e.g. after a language change) so later strings use the new language."""
        self.user = user
        return self
