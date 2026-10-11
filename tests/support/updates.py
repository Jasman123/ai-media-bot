"""Builders for Telegram Update payloads, shaped like real Bot API updates."""

import itertools
import time
from typing import Any

_update_ids = itertools.count(500_000)
_message_ids = itertools.count(1)


def _user(user_id: int, lang: str = "id", username: str | None = None) -> dict:
    user = {"id": user_id, "is_bot": False, "first_name": f"User{user_id}", "language_code": lang}
    if username:
        user["username"] = username
    return user


def _chat(chat_id: int, chat_type: str = "private") -> dict:
    if chat_type == "private":
        return {"id": chat_id, "type": "private", "first_name": f"User{chat_id}"}
    return {"id": chat_id, "type": chat_type, "title": "Test Group"}


def message(user_id: int, text: str | None = None, *, chat_id: int | None = None, chat_type: str = "private",
            lang: str = "id", photo_file_id: str | None = None, caption: str | None = None,
            voice_file_id: str | None = None, update_id: int | None = None, **extra: Any) -> dict:
    msg: dict[str, Any] = {
        "message_id": next(_message_ids),
        "date": int(time.time()),
        "from": _user(user_id, lang),
        "chat": _chat(chat_id or user_id, chat_type),
    }
    if text is not None:
        msg["text"] = text
        if text.startswith("/"):
            msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    if photo_file_id:
        msg["photo"] = [
            {"file_id": photo_file_id + "-small", "file_unique_id": "s", "width": 90, "height": 90},
            {"file_id": photo_file_id, "file_unique_id": "b", "width": 1024, "height": 1024, "file_size": 1000},
        ]
    if caption:
        msg["caption"] = caption
    if voice_file_id:
        msg["voice"] = {"file_id": voice_file_id, "file_unique_id": "v", "duration": 3, "mime_type": "audio/ogg"}
    msg.update(extra)
    return {"update_id": update_id or next(_update_ids), "message": msg}


def callback(user_id: int, data: str, *, message_id: int = 1, chat_id: int | None = None,
             chat_type: str = "private", lang: str = "id") -> dict:
    return {
        "update_id": next(_update_ids),
        "callback_query": {
            "id": f"cb{next(_update_ids)}",
            "from": _user(user_id, lang),
            "chat_instance": "ci",
            "data": data,
            "message": {
                "message_id": message_id,
                "date": int(time.time()),
                "chat": _chat(chat_id or user_id, chat_type),
                "text": "menu",
            },
        },
    }


def pre_checkout(user_id: int, payload: str, total_amount: int, *, currency: str = "XTR",
                 lang: str = "id") -> dict:
    return {
        "update_id": next(_update_ids),
        "pre_checkout_query": {
            "id": f"pcq{next(_update_ids)}",
            "from": _user(user_id, lang),
            "currency": currency,
            "total_amount": total_amount,
            "invoice_payload": payload,
        },
    }


def successful_payment(user_id: int, payload: str, total_amount: int, charge_id: str, *,
                       is_recurring: bool = False, is_first_recurring: bool = False,
                       subscription_expiration_date: int | None = None, lang: str = "id") -> dict:
    payment: dict[str, Any] = {
        "currency": "XTR",
        "total_amount": total_amount,
        "invoice_payload": payload,
        "telegram_payment_charge_id": charge_id,
        "provider_payment_charge_id": "",
    }
    if is_recurring:
        payment["is_recurring"] = True
    if is_first_recurring:
        payment["is_first_recurring"] = True
    if subscription_expiration_date:
        payment["subscription_expiration_date"] = subscription_expiration_date
    return message(user_id, lang=lang, successful_payment=payment)


def refunded_payment(user_id: int, payload: str, total_amount: int, charge_id: str) -> dict:
    return message(user_id, refunded_payment={
        "currency": "XTR",
        "total_amount": total_amount,
        "invoice_payload": payload,
        "telegram_payment_charge_id": charge_id,
    })


def inline_query(user_id: int, query: str, *, lang: str = "id") -> dict:
    return {
        "update_id": next(_update_ids),
        "inline_query": {"id": f"iq{next(_update_ids)}", "from": _user(user_id, lang), "query": query,
                         "offset": ""},
    }


def my_chat_member(user_id: int, chat_id: int, status: str = "member") -> dict:
    return {
        "update_id": next(_update_ids),
        "my_chat_member": {
            "chat": _chat(chat_id, "group"),
            "from": _user(user_id),
            "date": int(time.time()),
            "old_chat_member": {"status": "left", "user": {"id": 999000, "is_bot": True, "first_name": "Bot"}},
            "new_chat_member": {"status": status, "user": {"id": 999000, "is_bot": True, "first_name": "Bot"}},
        },
    }
