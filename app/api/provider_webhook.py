from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException, Request

from ..domain.errors import NotFound
from ..domain.generation import InvalidInput
from ..services.provider_webhooks import ProviderWebhookIntake

router = APIRouter(route_class=DishkaRoute)


@router.post("/webhook/provider/{provider}")
async def provider_webhook(provider: str, request: Request, intake: FromDishka[ProviderWebhookIntake]):
    try:
        handled = await intake.handle(provider, dict(request.headers), await request.body())
    except NotFound:
        raise HTTPException(status_code=404, detail="Unknown provider") from None
    except InvalidInput:
        raise HTTPException(status_code=401, detail="Invalid signature") from None
    return {"ok": True, "handled": handled}
