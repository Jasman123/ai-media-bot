"""Telegram channel layer. Core modules (jobs, billing, providers) must not import from here."""

from .router import Router


def build_router() -> Router:
    from .handlers import core

    router = Router()
    # Feature routers first; `core` holds the fallbacks and must stay last.
    for module in (core,):
        router.include(module.router)
    return router


BOT_COMMANDS = ("start", "menu", "help", "settings")
