import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from .. import telegram
from ..config import settings

router = APIRouter(prefix="/api")


def require_api_key(x_api_key: str | None = Header(default=None)):
    if not secrets.compare_digest(
        (x_api_key or "").encode(), settings.api_key.encode()
    ):
        raise HTTPException(status_code=401, detail="Invalid API key")


class SendRequest(BaseModel):
    chat_id: int
    text: str


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/send", dependencies=[Depends(require_api_key)])
async def send(body: SendRequest):
    return await telegram.send_message(body.chat_id, body.text)


@router.get("/webhook-info", dependencies=[Depends(require_api_key)])
async def webhook_info():
    return await telegram.get_webhook_info()
