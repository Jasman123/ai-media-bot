"""TelegramClient against a real HTTP server on localhost (real sockets, real multipart encoding)."""

import pytest

from app.telegram import InputFile, TelegramClient, TelegramError
from tests.support.fake_telegram import TEST_TOKEN, FakeTelegram
from tests.support.live_server import fake_telegram_asgi, serve


async def test_multipart_photo_upload_over_the_wire():
    fake = FakeTelegram()
    async with serve(fake_telegram_asgi(fake)) as base:
        client = TelegramClient(TEST_TOKEN, base_url=base, timeout=5)
        msg = await client.send_photo(42, InputFile("out.png", b"\x89PNG-bytes", "image/png"), caption="Hasil",
                                      reply_markup={"inline_keyboard": [[{"text": "A", "callback_data": "a"}]]})
        await client.aclose()
    call = fake.last("sendPhoto")
    assert call.files["photo"] == ("out.png", b"\x89PNG-bytes", "image/png")
    assert call.params["caption"] == "Hasil"
    assert call.params["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "a"
    assert msg["photo"][0]["file_id"]


async def test_shared_client_reuses_connections_for_many_calls():
    fake = FakeTelegram()
    async with serve(fake_telegram_asgi(fake)) as base:
        client = TelegramClient(TEST_TOKEN, base_url=base, timeout=5)
        for i in range(20):
            await client.send_message(1, f"m{i}")
        await client.aclose()
    assert len(fake.sent("sendMessage")) == 20


async def test_file_download_streams_from_file_endpoint():
    fake = FakeTelegram()
    file_id = fake.add_file(b"\xff\xd8jpeg" * 1000)
    async with serve(fake_telegram_asgi(fake)) as base:
        client = TelegramClient(TEST_TOKEN, base_url=base, timeout=5)
        info = await client.get_file(file_id)
        data = await client.download_file(info["file_path"])
        await client.aclose()
    assert data == b"\xff\xd8jpeg" * 1000


async def test_timeout_raises_telegram_error_without_token():
    fake = FakeTelegram()
    async with serve(fake_telegram_asgi(fake, delay_s=1.0)) as base:
        client = TelegramClient(TEST_TOKEN, base_url=base, timeout=0.2)
        with pytest.raises(TelegramError) as exc:
            await client.send_message(1, "slow")
        await client.aclose()
    assert "network error" in exc.value.description
    assert TEST_TOKEN not in str(exc.value)
