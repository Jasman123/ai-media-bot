"""Turns one Telegram update into a handler call with its dependencies injected."""

import logging
from dataclasses import dataclass

from dishka import AsyncContainer

from ..domain.ports import WizardState
from ..domain.users import TelegramIdentity
from ..i18n import I18n
from ..infra.telegram import TelegramClient, TelegramError
from ..services.users import UserService
from .context import Ctx
from .router import HandlerSpec, Router

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotConfig:
    username: str = ""


def _sender(update: dict) -> dict | None:
    for key in ("message", "callback_query", "pre_checkout_query", "inline_query", "my_chat_member"):
        if key in update:
            return update[key].get("from")
    return None


def parse_command(text: str, bot_username: str) -> tuple[str, list[str]] | None:
    if not text.startswith("/"):
        return None
    head, *rest = text.split()
    name, _, target = head[1:].partition("@")
    if target and bot_username and target.lower() != bot_username.lower():
        return None  # a command addressed to another bot in a group
    return name.lower(), rest


def _is_image_document(message: dict) -> bool:
    doc = message.get("document")
    return bool(doc and str(doc.get("mime_type", "")).startswith("image/"))


class Dispatcher:
    def __init__(
        self,
        router: Router,
        users: UserService,
        telegram: TelegramClient,
        i18n: I18n,
        state: WizardState,
        config: BotConfig,
        container: AsyncContainer,
    ):
        self._router = router
        self._users = users
        self._tg = telegram
        self._i18n = i18n
        self._state = state
        self._config = config
        self._container = container

    async def dispatch(self, update: dict) -> None:
        sender = _sender(update)
        if not sender or sender.get("is_bot"):
            return
        user, _ = await self._users.register_seen(TelegramIdentity(
            telegram_id=sender["id"], username=sender.get("username"), language_code=sender.get("language_code"),
        ))
        ctx = Ctx(update=update, user=user, tg=self._tg, i18n=self._i18n, state=self._state)
        try:
            await self._route(ctx, update)
        except TelegramError:
            raise  # the worker decides on retry (e.g. 429 retry_after)
        except Exception:
            logger.exception("Handler failed", extra={"update_id": update.get("update_id"), "user_id": user.id})
            if ctx.chat_id:
                try:
                    await ctx.reply(ctx.t("errors.generic"))
                except TelegramError:
                    pass

    async def _call(self, spec: HandlerSpec | None, ctx: Ctx) -> bool:
        if spec is None:
            return False
        deps = {name: await self._container.get(dep_type) for name, dep_type in spec.deps.items()}
        await spec.fn(ctx, **deps)
        return True

    async def _route(self, ctx: Ctx, update: dict) -> None:
        router = self._router
        banned = ctx.user.is_banned

        if "pre_checkout_query" in update:
            await self._call(router.events.get("pre_checkout"), ctx)
            return

        if "inline_query" in update:
            if not banned:
                await self._call(router.events.get("inline_query"), ctx)
            return

        if "my_chat_member" in update:
            chat = update["my_chat_member"]["chat"]
            ctx.chat_id, ctx.chat_type = chat["id"], chat["type"]
            await self._call(router.events.get("my_chat_member"), ctx)
            return

        if "callback_query" in update:
            query = update["callback_query"]
            ctx.callback = query
            if query.get("message"):
                ctx.chat_id = query["message"]["chat"]["id"]
                ctx.chat_type = query["message"]["chat"]["type"]
            if banned:
                await ctx.answer(ctx.t("errors.banned"), alert=True)
                return
            namespace, *ctx.args = (query.get("data") or "").split(":")
            if not await self._call(router.callbacks.get(namespace) or router.events.get("unknown_callback"), ctx):
                await ctx.answer()
            return

        message = update.get("message")
        if not message:
            return
        ctx.message = message
        ctx.chat_id = message["chat"]["id"]
        ctx.chat_type = message["chat"]["type"]

        # Payment service messages are processed even for banned users: money must never be lost.
        if "successful_payment" in message:
            await self._call(router.events.get("successful_payment"), ctx)
            return
        if "refunded_payment" in message:
            await self._call(router.events.get("refunded_payment"), ctx)
            return

        if banned:
            if ctx.is_private:
                await ctx.reply(ctx.t("errors.banned"))
            return

        text = message.get("text") or ""
        parsed = parse_command(text, self._config.username)
        if parsed:
            name, ctx.args = parsed
            await self._call(router.commands.get(name) or router.events.get("unknown_command"), ctx)
            return

        if not ctx.is_private:
            return  # in groups we only react to commands

        if "photo" in message or _is_image_document(message):
            spec = router.events.get("photo")
        elif "voice" in message:
            spec = router.events.get("voice")
        elif "video" in message:
            spec = router.events.get("video")
        elif text:
            step = (await self._state.get(ctx.user.id)).get("step") or ""
            spec = router.states.get(step) or router.events.get("text")
        else:
            spec = None
        await self._call(spec, ctx)
