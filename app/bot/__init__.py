"""Telegram interface layer: routing, presentation and handlers.

Handlers stay thin: parse input, call a service (injected by type), render the result. Core layers (domain,
services, infra) never import from here.
"""

from ..i18n import I18n
from ..infra.telegram import TelegramClient
from .router import Router

BOT_COMMANDS = ("start", "menu", "saldo", "help", "settings")


def build_router() -> Router:
    from .handlers import core, credits

    router = Router()
    for module in (credits, core):
        router.include(module.router)
    return router


async def register_commands(telegram: TelegramClient, i18n: I18n) -> None:
    for lang in ("id", "en"):
        commands = [{"command": c, "description": i18n.t(lang, f"commands.{c}")} for c in BOT_COMMANDS]
        await telegram.set_my_commands(commands, language_code=None if lang == "id" else lang)
