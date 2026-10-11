from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..infra.telegram import TelegramClient
from .security import require_api_key

router = APIRouter(prefix="/api", route_class=DishkaRoute)


class SendRequest(BaseModel):
    chat_id: int
    text: str


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/send", dependencies=[Depends(require_api_key)])
async def send(body: SendRequest, telegram: FromDishka[TelegramClient]):
    return await telegram.send_message(body.chat_id, body.text, parse_mode=None)


@router.get("/webhook-info", dependencies=[Depends(require_api_key)])
async def webhook_info(telegram: FromDishka[TelegramClient]):
    return await telegram.get_webhook_info()
