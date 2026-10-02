import logging

from . import telegram

logger = logging.getLogger(__name__)


async def handle_update(update: dict):
    try:
        await _process(update)
    except Exception:
        logger.exception("Failed to handle update %s", update.get("update_id"))


async def _process(update: dict):
    message = update.get("message")
    if not message:
        return  # ignore edits, callbacks, etc. for now

    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if text.startswith("/start"):
        await telegram.send_message(chat_id, "Hello! Send me something.")
    elif text:
        await telegram.send_message(chat_id, f"You said: {text}")  # replace with AI logic
    elif "photo" in message or "document" in message:
        await telegram.send_message(chat_id, "Media received.")
