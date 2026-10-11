"""Smoke-test the real entrypoints as subprocesses: `python -m app.worker` and `uvicorn app.main:app`.

They run from a temp cwd (so the developer's .env is never read), against the test Postgres/Redis and a fake
Bot API served on a real port.
"""

import asyncio
import os
import signal
import sys

import httpx

from app.queue import QUEUE_UPDATES
from tests.conftest import ROOT, TEST_REDIS_URL, WEBHOOK_SECRET
from tests.support import updates
from tests.support.fake_telegram import TEST_TOKEN, FakeTelegram
from tests.support.live_server import fake_telegram_asgi, free_port, serve


def _env(pg_url: str, tg_base: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BOT_", "WEBHOOK_", "API_", "DATABASE_"))}
    env.update(
        PYTHONPATH=str(ROOT), BOT_TOKEN=TEST_TOKEN, WEBHOOK_SECRET=WEBHOOK_SECRET, API_KEY="k", WEBHOOK_URL="",
        DATABASE_URL=pg_url, REDIS_URL=TEST_REDIS_URL, TELEGRAM_API_BASE=tg_base, LOG_JSON="true",
    )
    return env


async def _wait_for(predicate, timeout: float = 15.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return False


async def test_worker_process_consumes_updates(pg_url, redis_pool, clean, tmp_path):
    fake = FakeTelegram()
    async with serve(fake_telegram_asgi(fake)) as tg_base:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.worker", "updates", cwd=tmp_path, env=_env(pg_url, tg_base),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            await redis_pool.enqueue_job("process_update", updates.message(9101, "/start"),
                                         _queue_name=QUEUE_UPDATES)
            assert await _wait_for(lambda: fake.sent("sendMessage", 9101)), "worker did not process the update"
        finally:
            proc.send_signal(signal.SIGTERM)
            output, _ = await asyncio.wait_for(proc.communicate(), 20)
    assert "Pilih bahasa" in fake.last("sendMessage", 9101).text
    assert TEST_TOKEN.encode() not in output  # C7: no token in logs


async def test_api_process_serves_health_and_accepts_webhook(pg_url, redis_pool, clean, tmp_path):
    port = free_port()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), cwd=tmp_path,
        env=_env(pg_url, "http://127.0.0.1:9"), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http:
            async def healthy():
                try:
                    return (await http.get("/api/health")).status_code == 200
                except httpx.HTTPError:
                    return False

            for _ in range(100):
                if await healthy():
                    break
                await asyncio.sleep(0.1)
            resp = await http.post("/webhook/telegram", json=updates.message(9102, "/start"),
                                   headers={"X-Telegram-Bot-Api-Secret-Token": WEBHOOK_SECRET})
        assert resp.status_code == 200
        assert await redis_pool.zcard(QUEUE_UPDATES) == 1
    finally:
        proc.send_signal(signal.SIGTERM)
        await asyncio.wait_for(proc.communicate(), 20)
