"""Update routing (C5): commands, callback namespaces, wizard states, media and payment updates."""

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from ..services.users import get_or_create_user
from ..telegram import TelegramError
from .context import Ctx

if TYPE_CHECKING:
    from ..container import Container

logger = logging.getLogger(__name__)

Handler = Callable[[Ctx], Awaitable[None]]

EVENT_KINDS = {
    "text", "photo", "voice", "video", "pre_checkout", "successful_payment", "refunded_payment",
    "inline_query", "my_chat_member", "unknown_command", "unknown_callback",
}


class Router:
    def __init__(self) -> None:
        self.commands: dict[str, Handler] = {}
        self.callbacks: dict[str, Handler] = {}
        self.states: dict[str, Handler] = {}
        self.events: dict[str, Handler] = {}

    # ------------------------------------------------------------------ registration
    def command(self, *names: str):
        def deco(fn: Handler) -> Handler:
            for name in names:
                self.commands[name.lstrip("/").lower()] = fn
            return fn
        return deco

    def callback(self, namespace: str):
        def deco(fn: Handler) -> Handler:
            self.callbacks[namespace] = fn
            return fn
        return deco

    def state(self, step: str):
        """Free-text input while the user's wizard is at `step`."""
        def deco(fn: Handler) -> Handler:
            self.states[step] = fn
            return fn
        return deco

    def on(self, kind: str):
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind}")

        def deco(fn: Handler) -> Handler:
            self.events[kind] = fn
            return fn
        return deco

    def include(self, other: "Router") -> None:
        for name, table in (("commands", other.commands), ("callbacks", other.callbacks),
                            ("states", other.states), ("events", other.events)):
            mine = getattr(self, name)
            clash = set(mine) & set(table)
            if clash:
                raise ValueError(f"duplicate {name}: {sorted(clash)}")
            mine.update(table)


def _sender(update: dict) -> dict | None:
    for key in ("message", "callback_query", "pre_checkout_query", "inline_query", "my_chat_member"):
        if key in update:
            return update[key].get("from")
    return None


def _parse_command(text: str, bot_username: str) -> tuple[str, list[str]] | None:
    if not text.startswith("/"):
        return None
    head, *rest = text.split()
    name, _, target = head[1:].partition("@")
    if target and bot_username and target.lower() != bot_username.lower():
        return None  # a command addressed to another bot in a group
    return name.lower(), rest


async def dispatch(container: "Container", router: Router, update: dict) -> None:
    sender = _sender(update)
    if not sender or sender.get("is_bot"):
        return

    async with container.db() as session:
        user, _ = await get_or_create_user(
            session, sender["id"], username=sender.get("username"), language_code=sender.get("language_code")
        )
        await session.commit()

    ctx = Ctx(container=container, update=update, user=user)
    try:
        await _route(ctx, router, update)
    except TelegramError:
        raise  # let the worker decide on retry (e.g. 429 retry_after)
    except Exception:
        logger.exception("Handler failed", extra={"update_id": update.get("update_id"), "user_id": user.id})
        if ctx.chat_id:
            try:
                await ctx.reply(ctx.t("errors.generic"))
            except TelegramError:
                pass


async def _route(ctx: Ctx, router: Router, update: dict) -> None:
    banned = ctx.user is not None and ctx.user.is_banned

    if "pre_checkout_query" in update:
        handler = router.events.get("pre_checkout")
        if handler:
            await handler(ctx)
        return

    if "inline_query" in update:
        handler = router.events.get("inline_query")
        if handler and not banned:
            await handler(ctx)
        return

    if "my_chat_member" in update:
        handler = router.events.get("my_chat_member")
        if handler:
            ctx.chat_id = update["my_chat_member"]["chat"]["id"]
            ctx.chat_type = update["my_chat_member"]["chat"]["type"]
            await handler(ctx)
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
        namespace, *args = (query.get("data") or "").split(":")
        ctx.args = args
        handler = router.callbacks.get(namespace) or router.events.get("unknown_callback")
        if handler:
            await handler(ctx)
        else:
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
        handler = router.events.get("successful_payment")
        if handler:
            await handler(ctx)
        return
    if "refunded_payment" in message:
        handler = router.events.get("refunded_payment")
        if handler:
            await handler(ctx)
        return

    if banned:
        if ctx.is_private:
            await ctx.reply(ctx.t("errors.banned"))
        return

    text = message.get("text") or ""
    parsed = _parse_command(text, ctx.container.settings.bot_username)
    if parsed:
        name, ctx.args = parsed
        handler = router.commands.get(name) or router.events.get("unknown_command")
        if handler:
            await handler(ctx)
        return

    if not ctx.is_private:
        return  # in groups we only react to commands

    if "photo" in message or _is_image_document(message):
        handler = router.events.get("photo")
    elif "voice" in message:
        handler = router.events.get("voice")
    elif "video" in message:
        handler = router.events.get("video")
    elif text:
        state = await ctx.container.state.get(ctx.user.id)
        handler = router.states.get(state.get("step") or "") or router.events.get("text")
    else:
        handler = None
    if handler:
        await handler(ctx)


def _is_image_document(message: dict) -> bool:
    doc = message.get("document")
    return bool(doc and str(doc.get("mime_type", "")).startswith("image/"))
