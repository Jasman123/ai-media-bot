"""Telegram implementation of the DeliveryChannel port: results, failure notices and progress edits.

The core hands over files and reason codes; all wording, keyboards and Telegram limits live here.
"""

import logging
from typing import Any

from ..domain.jobs import ChannelUnreachable, FailureReason, Job, MediaFile, Progress
from ..domain.ports import Storage
from ..i18n import I18n
from ..infra.telegram import InputFile, TelegramClient, TelegramError
from .keyboards import btn, cb, kb

logger = logging.getLogger(__name__)

PHOTO_LIMIT = 10 * 1024 * 1024     # Bot API photo upload limit
UPLOAD_LIMIT = 50 * 1024 * 1024    # cloud Bot API upload limit for video/document
LINK_TTL_S = 24 * 3600
UNREACHABLE = ("bot was blocked", "user is deactivated", "chat not found", "bot was kicked")


def _unreachable(exc: TelegramError) -> bool:
    return exc.error_code == 403 or any(marker in exc.description.lower() for marker in UNREACHABLE)


class TelegramDelivery:
    def __init__(self, telegram: TelegramClient, i18n: I18n, storage: Storage):
        self._tg = telegram
        self._i18n = i18n
        self._storage = storage

    def _t(self, job: Job, key: str, **kwargs: Any) -> str:
        return self._i18n.t(job.chat_ref.get("lang"), key, **kwargs)

    # ------------------------------------------------------------------ DeliveryChannel
    async def deliver(self, job: Job, files: list[MediaFile]) -> dict[str, Any]:
        chat_id = job.chat_ref["chat_id"]
        caption = self._t(job, "job.done", credits=job.credits_charged)
        markup = self.result_keyboard(job)
        file_ids: list[str] = []
        try:
            await self._finish_progress(job)
            if len(files) > 1 and all(f.kind == files[0].kind and len(f.content) <= PHOTO_LIMIT for f in files):
                kind = "photo" if files[0].mime.startswith("image/") else "video"
                messages = await self._tg.send_media_group(
                    chat_id, [(kind, InputFile(f.filename, f.content, f.mime)) for f in files[:10]])
                file_ids += [self._file_id(m) for m in messages]
                await self._tg.send_message(chat_id, caption, reply_markup=markup)
            else:
                for index, file in enumerate(files):
                    last = index == len(files) - 1
                    message = await self._send_one(chat_id, file, caption if last else None,
                                                   markup if last else None)
                    if message:
                        file_ids.append(self._file_id(message))
        except TelegramError as exc:
            if _unreachable(exc):
                raise ChannelUnreachable(str(exc)) from exc
            raise
        return {"file_ids": [f for f in file_ids if f], "kind": files[0].kind.value if files else None}

    async def failed(self, job: Job, reason: FailureReason, refunded: bool) -> None:
        text = self._t(job, f"job.failed.{reason.value}")
        if refunded:
            text += "\n\n" + self._t(job, "job.refunded", credits=job.credits_charged)
        await self._say(job, text, kb([[btn(self._t(job, "menu.back"), cb("menu", "home"))]]))

    async def progress(self, job: Job, progress: Progress) -> None:
        message_id = job.chat_ref.get("progress_message_id")
        if not message_id:
            return
        if progress.state == "queued" and progress.queue_position:
            text = self._t(job, "job.progress_queued", position=progress.queue_position)
        else:
            text = self._t(job, "job.progress_running", eta=progress.eta_s or "?")
        try:
            await self._tg.edit_message_text(job.chat_ref["chat_id"], message_id, text)
        except TelegramError as exc:
            if _unreachable(exc):
                raise ChannelUnreachable(str(exc)) from exc

    # ------------------------------------------------------------------ helpers
    def result_keyboard(self, job: Job) -> dict | None:
        return None  # result actions (F4) are added by the result-actions feature

    async def _send_one(self, chat_id: int, file: MediaFile, caption: str | None, markup: dict | None) -> dict | None:
        upload = InputFile(file.filename, file.content, file.mime)
        size = len(file.content)
        if file.mime.startswith("image/") and size <= PHOTO_LIMIT:
            return await self._tg.send_photo(chat_id, upload, caption=caption, reply_markup=markup)
        if file.mime.startswith("video/") and size <= UPLOAD_LIMIT:
            return await self._tg.send_video(chat_id, upload, caption=caption, reply_markup=markup)
        if size <= UPLOAD_LIMIT:
            return await self._tg.send_document(chat_id, upload, caption=caption, reply_markup=markup)
        link = await self._storage.url(file.storage_key, LINK_TTL_S)
        lang_caption = caption or ""
        await self._tg.send_message(chat_id, f"{lang_caption}\n\n🔗 {link}".strip(), reply_markup=markup)
        return None

    @staticmethod
    def _file_id(message: dict) -> str | None:
        if message.get("photo"):
            return message["photo"][-1]["file_id"]
        for key in ("video", "document", "animation"):
            if message.get(key):
                return message[key]["file_id"]
        return None

    async def _finish_progress(self, job: Job) -> None:
        message_id = job.chat_ref.get("progress_message_id")
        if message_id:
            try:
                await self._tg.delete_message(job.chat_ref["chat_id"], message_id)
            except TelegramError:
                pass

    async def _say(self, job: Job, text: str, markup: dict | None) -> None:
        chat_id = job.chat_ref["chat_id"]
        message_id = job.chat_ref.get("progress_message_id")
        try:
            if message_id:
                try:
                    await self._tg.edit_message_text(chat_id, message_id, text, reply_markup=markup)
                    return
                except TelegramError as exc:
                    if _unreachable(exc):
                        raise
            await self._tg.send_message(chat_id, text, reply_markup=markup)
        except TelegramError as exc:
            if _unreachable(exc):
                raise ChannelUnreachable(str(exc)) from exc
            raise
