import logging

from .container import Container

logger = logging.getLogger(__name__)


async def handle_update(ctx: Container, update: dict):
    try:
        await _process(ctx, update)
    except Exception:
        logger.exception("Failed to handle update %s", update.get("update_id"))


async def _process(ctx: Container, update: dict):
    message = update.get("message")
    if not message:
        return  # callbacks and payments are routed in app/bot (next feature)

    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if text.startswith("/start"):
        await ctx.telegram.send_message(chat_id, "Hello! Send me something.", parse_mode=None)
    elif text:
        await ctx.telegram.send_message(chat_id, f"You said: {text}", parse_mode=None)
    elif "photo" in message or "document" in message:
        await ctx.telegram.send_message(chat_id, "Media received.", parse_mode=None)
