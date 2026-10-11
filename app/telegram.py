"""Telegram Bot API client.

One shared httpx.AsyncClient per process (C4), multipart uploads for media, and errors that
never carry the bot token (C7: the token is part of every Bot API URL).
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# C1: pre_checkout_query is required for Stars payments (answer within 10 s).
ALLOWED_UPDATES = [
    "message",
    "callback_query",
    "pre_checkout_query",
    "inline_query",
    "my_chat_member",
]


class TelegramError(Exception):
    def __init__(self, method: str, error_code: int, description: str, retry_after: int | None = None):
        super().__init__(f"Telegram {method} failed ({error_code}): {description}")
        self.method = method
        self.error_code = error_code
        self.description = description
        self.retry_after = retry_after


@dataclass(frozen=True)
class InputFile:
    filename: str
    content: bytes
    mime_type: str = "application/octet-stream"


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if v is not None}


class TelegramClient:
    def __init__(
        self,
        token: str,
        base_url: str = "https://api.telegram.org",
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base = f"{base_url.rstrip('/')}/bot{token}"
        self._file_base = f"{base_url.rstrip('/')}/file/bot{token}"
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            transport=transport,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ transport
    async def call(self, method: str, **params: Any) -> Any:
        try:
            resp = await self._http.post(f"{self._base}/{method}", json=_clean(params))
        except httpx.HTTPError as exc:
            # str(exc) can include the request URL; report only the exception type.
            raise TelegramError(method, 0, f"network error: {type(exc).__name__}") from None
        return self._result(method, resp)

    async def call_multipart(self, method: str, files: dict[str, InputFile], **params: Any) -> Any:
        data = {}
        for key, value in _clean(params).items():
            data[key] = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        upload = {name: (f.filename, f.content, f.mime_type) for name, f in files.items()}
        try:
            resp = await self._http.post(f"{self._base}/{method}", data=data, files=upload)
        except httpx.HTTPError as exc:
            raise TelegramError(method, 0, f"network error: {type(exc).__name__}") from None
        return self._result(method, resp)

    @staticmethod
    def _result(method: str, resp: httpx.Response) -> Any:
        try:
            data = resp.json()
        except ValueError:
            raise TelegramError(method, resp.status_code, "non-JSON response") from None
        if not data.get("ok"):
            retry_after = (data.get("parameters") or {}).get("retry_after")
            raise TelegramError(
                method, data.get("error_code", resp.status_code), data.get("description", ""), retry_after
            )
        return data["result"]

    async def _send_media(self, method: str, field: str, chat_id: int, media: str | InputFile, **params: Any):
        if isinstance(media, InputFile):
            return await self.call_multipart(method, {field: media}, chat_id=chat_id, **params)
        return await self.call(method, chat_id=chat_id, **{field: media}, **params)

    # ------------------------------------------------------------------ messages
    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str | None = "HTML",
        disable_notification: bool | None = None,
        link_preview: bool = False,
    ) -> dict:
        return await self.call(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_notification=disable_notification,
            link_preview_options=None if link_preview else {"is_disabled": True},
        )

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: dict | None = None,
        parse_mode: str | None = "HTML",
    ) -> Any:
        try:
            return await self.call(
                "editMessageText",
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                link_preview_options={"is_disabled": True},
            )
        except TelegramError as exc:
            # Editing to identical content is not an error worth surfacing.
            if "message is not modified" in exc.description:
                return None
            raise

    async def edit_message_reply_markup(self, chat_id: int, message_id: int, reply_markup: dict | None):
        try:
            return await self.call(
                "editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup=reply_markup
            )
        except TelegramError as exc:
            if "message is not modified" in exc.description:
                return None
            raise

    async def delete_message(self, chat_id: int, message_id: int) -> Any:
        return await self.call("deleteMessage", chat_id=chat_id, message_id=message_id)

    async def answer_callback_query(
        self, callback_query_id: str, text: str | None = None, show_alert: bool = False
    ) -> Any:
        return await self.call(
            "answerCallbackQuery", callback_query_id=callback_query_id, text=text, show_alert=show_alert or None
        )

    async def send_chat_action(self, chat_id: int, action: str) -> Any:
        return await self.call("sendChatAction", chat_id=chat_id, action=action)

    # ------------------------------------------------------------------ media
    async def send_photo(self, chat_id: int, photo: str | InputFile, caption: str | None = None,
                         reply_markup: dict | None = None, disable_notification: bool | None = None) -> dict:
        return await self._send_media("sendPhoto", "photo", chat_id, photo, caption=caption,
                                      parse_mode="HTML" if caption else None, reply_markup=reply_markup,
                                      disable_notification=disable_notification)

    async def send_video(self, chat_id: int, video: str | InputFile, caption: str | None = None,
                         reply_markup: dict | None = None, disable_notification: bool | None = None) -> dict:
        return await self._send_media("sendVideo", "video", chat_id, video, caption=caption,
                                      parse_mode="HTML" if caption else None, reply_markup=reply_markup,
                                      supports_streaming=True, disable_notification=disable_notification)

    async def send_document(self, chat_id: int, document: str | InputFile, caption: str | None = None,
                            reply_markup: dict | None = None, disable_notification: bool | None = None) -> dict:
        return await self._send_media("sendDocument", "document", chat_id, document, caption=caption,
                                      parse_mode="HTML" if caption else None, reply_markup=reply_markup,
                                      disable_notification=disable_notification)

    async def get_file(self, file_id: str) -> dict:
        return await self.call("getFile", file_id=file_id)

    async def download_file(self, file_path: str, max_bytes: int = 20 * 1024 * 1024) -> bytes:
        try:
            async with self._http.stream("GET", f"{self._file_base}/{file_path}") as resp:
                if resp.status_code != 200:
                    raise TelegramError("downloadFile", resp.status_code, "download failed")
                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise TelegramError("downloadFile", 413, "file too large")
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise TelegramError("downloadFile", 0, f"network error: {type(exc).__name__}") from None
        return b"".join(chunks)

    # ------------------------------------------------------------------ bot setup
    async def get_me(self) -> dict:
        return await self.call("getMe")

    async def set_webhook(self, url: str, secret: str, drop_pending_updates: bool = False) -> Any:
        return await self.call(
            "setWebhook",
            url=url,
            secret_token=secret,
            allowed_updates=ALLOWED_UPDATES,
            drop_pending_updates=drop_pending_updates,
        )

    async def delete_webhook(self) -> Any:
        return await self.call("deleteWebhook")

    async def get_webhook_info(self) -> dict:
        return await self.call("getWebhookInfo")

    async def set_my_commands(self, commands: list[dict], language_code: str | None = None) -> Any:
        return await self.call("setMyCommands", commands=commands, language_code=language_code)
