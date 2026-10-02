import secrets

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from ..config import settings
from ..handlers import handle_update

router = APIRouter()


@router.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if not secrets.compare_digest(
        (x_telegram_bot_api_secret_token or "").encode(),
        settings.webhook_secret.encode(),
    ):
        raise HTTPException(status_code=403, detail="Invalid secret")

    update = await request.json()
    background_tasks.add_task(handle_update, update)
    return {"ok": True}
