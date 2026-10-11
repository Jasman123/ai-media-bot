import logging
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ..container import Container
from ..deps import get_container
from ..queue import QUEUE_UPDATES

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    ctx: Container = Depends(get_container),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if not secrets.compare_digest(
        (x_telegram_bot_api_secret_token or "").encode(),
        ctx.settings.webhook_secret.encode(),
    ):
        raise HTTPException(status_code=403, detail="Invalid secret")

    update = await request.json()
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        raise HTTPException(status_code=400, detail="Missing update_id")

    if not await ctx.deduper.first_seen(update_id):
        return {"ok": True, "duplicate": True}

    # C3: durable queue instead of in-process BackgroundTasks. If Redis is down we answer 503 so Telegram
    # redelivers the update later instead of it being lost.
    try:
        await ctx.redis.enqueue_job(
            "process_update", update, _job_id=f"upd:{update_id}", _queue_name=QUEUE_UPDATES
        )
    except Exception:
        logger.exception("Failed to enqueue update", extra={"update_id": update_id})
        try:
            await ctx.deduper.forget(update_id)
        except Exception:  # noqa: S110 - Redis is already failing; Telegram will retry anyway
            pass
        raise HTTPException(status_code=503, detail="Queue unavailable") from None
    return {"ok": True}
