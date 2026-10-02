import httpx
from .config import settings


BASE = f"https://api.telegram.org/bot{settings.bot_token}"

async def call(method: str, **payload):
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{BASE}/{method}", json=payload)
    data = resp.json()

    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data}")
    return data["result"]

async def send_message(chat_id: int, text: str):
    return await call("sendMessage", chat_id=chat_id, text=text)

async def set_webhook(url: str, secret: str):
    return await call(
        "setWebhook",
        url=url,
        secret_token=secret,
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )

async def delete_webhook():
    return await call("deleteWebhook")


async def get_webhook_info():
    return await call("getWebhookInfo")