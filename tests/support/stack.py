"""Full-stack harness: real FastAPI app + real Postgres + real Redis/ARQ + fake Telegram (and fake providers).

    user = stack.user(7001)
    await user.send("/start")          # posts a webhook update, then drains the queues with burst workers
    await user.press("lang:id")        # presses an inline button
    assert "Mau buat apa" in user.last_text()
"""

import contextlib
import itertools

import httpx
from arq.connections import ArqRedis
from dishka import AsyncContainer
from sqlalchemy.ext.asyncio import AsyncEngine

from app.domain.tasks import ALL_QUEUES
from app.infra.db import make_sessionmaker
from app.worker import FUNCTIONS, create_worker
from tests.conftest import WEBHOOK_SECRET
from tests.support import updates
from tests.support.fake_telegram import FakeTelegram


class Stack:
    def __init__(self, di: AsyncContainer, tg: FakeTelegram, http: httpx.AsyncClient, redis: ArqRedis,
                 engine: AsyncEngine):
        self.di = di
        self.tg = tg
        self.http = http
        self.redis = redis
        self.sessionmaker = make_sessionmaker(engine)
        self._msg_ids = itertools.count(10_000)

    @contextlib.asynccontextmanager
    async def scope(self):
        """A REQUEST scope, like one webhook call or one worker job: `async with stack.scope() as r: await r.get(T)`."""
        async with self.di() as request_container:
            yield request_container

    async def post_update(self, update: dict, secret: str = WEBHOOK_SECRET) -> httpx.Response:
        return await self.http.post("/webhook/telegram", json=update,
                                    headers={"X-Telegram-Bot-Api-Secret-Token": secret})

    async def drain(self, max_rounds: int = 50) -> int:
        """Run burst workers on every queue until all are empty. Raises if any job failed."""
        done = 0
        for _ in range(max_rounds):
            pending = 0
            for queue in ALL_QUEUES:
                if not FUNCTIONS[queue]:
                    continue
                if await self.redis.zcard(queue) == 0:
                    continue
                pending += 1
                worker = create_worker(self.di, self.redis, queue, burst=True, poll_delay=0.01)
                done += await worker.run_check()
            if pending == 0:
                return done
        raise AssertionError("queues did not drain")

    async def send_update(self, update: dict) -> httpx.Response:
        resp = await self.post_update(update)
        assert resp.status_code == 200, resp.text
        await self.drain()
        return resp

    def user(self, telegram_id: int, lang: str = "id") -> "BotUser":
        return BotUser(self, telegram_id, lang)


class BotUser:
    def __init__(self, stack: Stack, telegram_id: int, lang: str = "id"):
        self.stack = stack
        self.id = telegram_id
        self.lang = lang

    @property
    def tg(self) -> FakeTelegram:
        return self.stack.tg

    async def send(self, text: str, **kwargs) -> None:
        await self.stack.send_update(updates.message(self.id, text, lang=self.lang, **kwargs))

    async def press(self, data: str, message_id: int | None = None) -> None:
        if message_id is None:
            message_id = self._last_markup_message_id()
        await self.stack.send_update(updates.callback(self.id, data, message_id=message_id, lang=self.lang))

    async def press_button(self, text_contains: str) -> None:
        call = self.tg.last_markup_call(self.id)
        await self.press(call.button_data(text_contains))

    async def photo(self, content: bytes = b"\xff\xd8\xff\xe0fake-jpeg", caption: str | None = None) -> str:
        file_id = self.tg.add_file(content)
        await self.stack.send_update(updates.message(self.id, None, photo_file_id=file_id, caption=caption,
                                                     lang=self.lang))
        return file_id

    def calls(self, method: str | None = None):
        return self.tg.sent(method, self.id)

    def texts(self) -> list[str]:
        return self.tg.texts(self.id)

    def last_text(self) -> str:
        texts = self.texts()
        assert texts, "no messages sent to this user"
        return texts[-1]

    def last_markup(self):
        return self.tg.last_markup_call(self.id)

    def _last_markup_message_id(self) -> int:
        try:
            call = self.tg.last_markup_call(self.id)
        except AssertionError:
            return 1
        return int(call.params.get("message_id") or 1)
