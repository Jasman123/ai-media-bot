import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request

from ..container import Container
from ..deps import get_container
from ..handlers import handle_update

router = APIRouter()


@router.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    ctx: Container = Depends(get_container),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if not secrets.compare_digest(
        (x_telegram_bot_api_secret_token or "").encode(),
        ctx.settings.webhook_secret.encode(),
    ):
        raise HTTPException(status_code=403, detail="Invalid secret")

    update = await request.json()
    background_tasks.add_task(handle_update, ctx, update)
    return {"ok": True}
