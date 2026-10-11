"""In-memory Telegram Bot API for tests.

Plugs into TelegramClient through an httpx.MockTransport, records every call (JSON or multipart),
returns realistic results and serves file downloads for getFile paths.
"""

import itertools
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from email.parser import BytesParser
from email.policy import default as default_policy
from typing import Any

import httpx

from app.infra.telegram import TelegramClient

TEST_TOKEN = "123456789:TEST_TOKEN_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BOT_ID = 999000
BOT_USERNAME = "test_media_bot"


@dataclass
class Call:
    method: str
    params: dict[str, Any]
    files: dict[str, tuple[str, bytes, str]] = field(default_factory=dict)

    @property
    def chat_id(self) -> int | None:
        value = self.params.get("chat_id")
        return int(value) if value is not None else None

    @property
    def text(self) -> str:
        return str(self.params.get("text") or self.params.get("caption") or "")

    @property
    def markup(self) -> dict | None:
        return self.params.get("reply_markup")

    def buttons(self) -> list[dict]:
        markup = self.markup or {}
        return [b for row in markup.get("inline_keyboard", []) for b in row]

    def callback_data(self) -> list[str]:
        return [b["callback_data"] for b in self.buttons() if "callback_data" in b]

    def button_data(self, text_contains: str) -> str:
        for b in self.buttons():
            if text_contains.lower() in b["text"].lower() and "callback_data" in b:
                return b["callback_data"]
        raise AssertionError(f"No button containing {text_contains!r} in {[b['text'] for b in self.buttons()]}")


def _maybe_json(value: str) -> Any:
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


class FakeTelegram:
    def __init__(self, token: str = TEST_TOKEN):
        self.token = token
        self.calls: list[Call] = []
        self.files: dict[str, bytes] = {}          # file_path -> content
        self.file_paths: dict[str, str] = {}       # file_id -> file_path
        self.failures: dict[str, list[dict]] = {}  # method -> queued error payloads
        self.overrides: dict[str, Callable[[Call], Any]] = {}
        self.star_transactions: list[dict] = []
        self.star_balance = 0
        self._ids = itertools.count(1000)

    # ------------------------------------------------------------------ wiring
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def client(self) -> TelegramClient:
        return TelegramClient(self.token, "https://api.telegram.org", timeout=5, transport=self.transport())

    def fail_next(self, method: str, error_code: int = 400, description: str = "Bad Request",
                  retry_after: int | None = None, times: int = 1) -> None:
        payload = {"ok": False, "error_code": error_code, "description": description}
        if retry_after is not None:
            payload["parameters"] = {"retry_after": retry_after}
        self.failures.setdefault(method, []).extend([payload] * times)

    def add_file(self, content: bytes, prefix: str = "photo", ext: str = "jpg") -> str:
        n = next(self._ids)
        file_id = f"{prefix}-file-{n}"
        path = f"{prefix}s/file_{n}.{ext}"
        self.file_paths[file_id] = path
        self.files[path] = content
        return file_id

    # ------------------------------------------------------------------ queries
    def sent(self, method: str | None = None, chat_id: int | None = None) -> list[Call]:
        return [
            c for c in self.calls
            if (method is None or c.method == method) and (chat_id is None or c.chat_id == chat_id)
        ]

    def last(self, method: str, chat_id: int | None = None) -> Call:
        calls = self.sent(method, chat_id)
        assert calls, f"no {method} call recorded (chat_id={chat_id}); got {[c.method for c in self.calls]}"
        return calls[-1]

    def texts(self, chat_id: int | None = None) -> list[str]:
        out = []
        for c in self.calls:
            if chat_id is not None and c.chat_id != chat_id:
                continue
            if c.method in {"sendMessage", "editMessageText", "sendPhoto", "sendVideo", "sendDocument"}:
                out.append(c.text)
        return out

    def last_markup_call(self, chat_id: int | None = None) -> Call:
        for c in reversed(self.calls):
            if c.markup and (chat_id is None or c.chat_id == chat_id):
                return c
        raise AssertionError("no call with reply_markup recorded")

    def reset(self) -> None:
        self.calls.clear()

    # ------------------------------------------------------------------ handler
    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        file_prefix = f"/file/bot{self.token}/"
        if path.startswith(file_prefix):
            content = self.files.get(path[len(file_prefix):])
            return httpx.Response(200, content=content) if content is not None else httpx.Response(404)

        prefix = f"/bot{self.token}/"
        if not path.startswith(prefix):
            return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
        method = path[len(prefix):]
        call = self._parse(method, request)
        self.calls.append(call)

        queued = self.failures.get(method)
        if queued:
            return httpx.Response(200, json=queued.pop(0))
        if method in self.overrides:
            return httpx.Response(200, json={"ok": True, "result": self.overrides[method](call)})
        return httpx.Response(200, json={"ok": True, "result": self._result(call)})

    @staticmethod
    def _parse(method: str, request: httpx.Request) -> Call:
        ctype = request.headers.get("content-type", "")
        body = request.content
        if ctype.startswith("multipart/form-data"):
            msg = BytesParser(policy=default_policy).parsebytes(
                b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body
            )
            params, files = {}, {}
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                filename = part.get_filename()
                payload = part.get_payload(decode=True) or b""
                if filename:
                    files[name] = (filename, payload, part.get_content_type())
                else:
                    params[name] = _maybe_json(payload.decode())
            return Call(method, params, files)
        params = json.loads(body) if body else {}
        return Call(method, params)

    def _message(self, call: Call, **extra) -> dict:
        chat_id = call.chat_id or 0
        msg = {
            "message_id": next(self._ids),
            "date": int(time.time()),
            "chat": {"id": chat_id, "type": "private" if chat_id > 0 else "group"},
            "from": {"id": BOT_ID, "is_bot": True, "first_name": "Test", "username": BOT_USERNAME},
        }
        msg.update(extra)
        return msg

    def _media_meta(self, call: Call, field_name: str, prefix: str) -> dict:
        n = next(self._ids)
        size = len(call.files[field_name][1]) if field_name in call.files else 0
        return {"file_id": f"{prefix}-sent-{n}", "file_unique_id": f"u{n}", "file_size": size}

    def _result(self, call: Call) -> Any:
        m = call.method
        if m == "getMe":
            return {"id": BOT_ID, "is_bot": True, "first_name": "Test", "username": BOT_USERNAME}
        if m == "sendMessage":
            return self._message(call, text=call.params.get("text", ""))
        if m == "sendPhoto":
            meta = self._media_meta(call, "photo", "photo")
            return self._message(call, photo=[{**meta, "width": 1024, "height": 1024}],
                                 caption=call.params.get("caption"))
        if m == "sendVideo":
            return self._message(call, video={**self._media_meta(call, "video", "video"), "duration": 5,
                                              "width": 720, "height": 1280}, caption=call.params.get("caption"))
        if m == "sendDocument":
            return self._message(call, document=self._media_meta(call, "document", "doc"),
                                 caption=call.params.get("caption"))
        if m == "sendMediaGroup":
            media = call.params.get("media") or []
            return [self._message(call, photo=[{"file_id": f"photo-sent-{next(self._ids)}",
                                                "file_unique_id": "x", "width": 1, "height": 1}])
                    for _ in media]
        if m in {"editMessageText", "editMessageReplyMarkup", "editMessageCaption"}:
            return self._message(call, text=call.params.get("text", ""))
        if m == "sendInvoice":
            return self._message(call, invoice={"title": call.params.get("title"),
                                                "total_amount": call.params["prices"][0]["amount"],
                                                "currency": call.params.get("currency")})
        if m == "getFile":
            file_id = call.params["file_id"]
            path = self.file_paths.get(file_id)
            if path is None:
                return {"file_id": file_id, "file_unique_id": "x"}
            return {"file_id": file_id, "file_unique_id": "x", "file_size": len(self.files[path]),
                    "file_path": path}
        if m == "getWebhookInfo":
            return {"url": "", "has_custom_certificate": False, "pending_update_count": 0}
        if m == "getStarTransactions":
            offset = int(call.params.get("offset") or 0)
            limit = int(call.params.get("limit") or 100)
            return {"transactions": self.star_transactions[offset:offset + limit]}
        if m == "getMyStarBalance":
            return {"amount": self.star_balance}
        if m == "createInvoiceLink":
            return f"https://t.me/$invoice-{next(self._ids)}"
        return True
