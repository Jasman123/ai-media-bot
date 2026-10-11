import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .container import Container, build_container
from .logging_setup import configure_logging
from .routers import api, webhook

logger = logging.getLogger(__name__)


def create_app(container: Container | None = None) -> FastAPI:
    """App factory. Pass a prebuilt container in tests; production builds one from settings."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned = container is None
        ctx = container or build_container(get_settings())
        if owned:
            configure_logging(ctx.settings.log_level, ctx.settings.log_json)
        app.state.container = ctx
        settings = ctx.settings
        if settings.webhook_url and settings.set_webhook_on_startup:
            await ctx.telegram.set_webhook(
                f"{settings.webhook_url.rstrip('/')}/webhook/telegram",
                settings.webhook_secret,
                drop_pending_updates=settings.drop_pending_updates,
            )
            logger.info("Webhook registered")
        try:
            yield
        finally:
            if owned:
                await ctx.aclose()

    app = FastAPI(title="ai-media-bot", lifespan=lifespan)
    app.include_router(webhook.router)
    app.include_router(api.router)
    return app


app = create_app()
