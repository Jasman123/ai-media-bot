"""HTTP entrypoint: `uvicorn app.main:app`. Importing this module never reads env vars; the DI graph is
validated here but nothing is resolved until startup."""

import logging
from contextlib import asynccontextmanager

from dishka import AsyncContainer
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI

from .api import system, webhook
from .bot import register_commands
from .config import Settings
from .di import make_container, warm_up
from .i18n import I18n
from .infra.telegram import TelegramClient
from .logging_setup import configure_logging

logger = logging.getLogger(__name__)


def create_app(container: AsyncContainer | None = None) -> FastAPI:
    """App factory. Tests pass a container built with override providers; it is then closed by the caller."""
    owned = container is None
    di = container or make_container()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = await di.get(Settings)
        if owned:
            configure_logging(settings.log_level, settings.log_json)
        # Fail fast: connect Postgres/Redis now rather than inside the first webhook call.
        await warm_up(di)
        if settings.webhook_url and settings.set_webhook_on_startup:
            telegram = await di.get(TelegramClient)
            await telegram.set_webhook(
                f"{settings.webhook_url.rstrip('/')}/webhook/telegram",
                settings.webhook_secret,
                drop_pending_updates=settings.drop_pending_updates,
            )
            await register_commands(telegram, await di.get(I18n))
            logger.info("Webhook and commands registered")
        try:
            yield
        finally:
            if owned:
                await di.close()

    app = FastAPI(title="ai-media-bot", lifespan=lifespan)
    app.include_router(webhook.router)
    app.include_router(system.router)
    setup_dishka(di, app)
    return app


app = create_app()
