from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import telegram
from .config import settings
from .routers import api, webhook


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.webhook_url:
        await telegram.set_webhook(
            f"{settings.webhook_url}/webhook/telegram", settings.webhook_secret
        )
    yield
    # optional: await telegram.delete_webhook()


app = FastAPI(title="ai-media-bot", lifespan=lifespan)
app.include_router(webhook.router)
app.include_router(api.router)
