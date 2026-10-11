from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..container import Container
from ..deps import get_container, require_api_key

router = APIRouter(prefix="/api")


class SendRequest(BaseModel):
    chat_id: int
    text: str


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/send", dependencies=[Depends(require_api_key)])
async def send(body: SendRequest, ctx: Container = Depends(get_container)):
    return await ctx.telegram.send_message(body.chat_id, body.text, parse_mode=None)


@router.get("/webhook-info", dependencies=[Depends(require_api_key)])
async def webhook_info(ctx: Container = Depends(get_container)):
    return await ctx.telegram.get_webhook_info()
