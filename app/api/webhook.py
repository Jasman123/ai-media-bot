from dishka.integrations.fastapi import DishkaRoute, FromDishka, inject
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ..config import Settings
from ..domain.errors import InvalidUpdate, QueueUnavailable
from ..services.updates import UpdateIntake
from .security import constant_time_equals

router = APIRouter(route_class=DishkaRoute)


@inject
async def verify_telegram_secret(
    settings: FromDishka[Settings],
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> None:
    """Runs before the endpoint's own dependencies are resolved, so unauthenticated calls cost nothing."""
    if not constant_time_equals(x_telegram_bot_api_secret_token, settings.webhook_secret):
        raise HTTPException(status_code=403, detail="Invalid secret")


@router.post("/webhook/telegram", dependencies=[Depends(verify_telegram_secret)])
async def telegram_webhook(request: Request, intake: FromDishka[UpdateIntake]):
    try:
        accepted = await intake.accept(await request.json())
    except InvalidUpdate:
        raise HTTPException(status_code=400, detail="Missing update_id") from None
    except QueueUnavailable:
        # 503 makes Telegram redeliver the update later instead of it being lost (C3).
        raise HTTPException(status_code=503, detail="Queue unavailable") from None
    return {"ok": True} if accepted else {"ok": True, "duplicate": True}
