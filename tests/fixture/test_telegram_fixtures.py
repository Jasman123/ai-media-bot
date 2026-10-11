"""Recorded Bot API payloads: error bodies and real-shaped updates."""

import httpx
import pytest

from app.handlers import handle_update
from app.telegram import TelegramClient, TelegramError
from tests.conftest import load_fixture
from tests.support.fake_telegram import TEST_TOKEN


@pytest.mark.parametrize(
    "name,code,retry_after",
    [("too_many_requests", 429, 7), ("blocked", 403, None), ("chat_not_found", 400, None)],
)
async def test_recorded_error_bodies_map_to_telegram_error(name, code, retry_after):
    body = load_fixture("telegram", "api_errors.json")[name]
    client = TelegramClient(TEST_TOKEN, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(TelegramError) as exc:
        await client.send_message(1, "x")
    assert exc.value.error_code == code
    assert exc.value.retry_after == retry_after
    await client.aclose()


async def test_recorded_not_modified_is_not_an_error():
    body = load_fixture("telegram", "api_errors.json")["not_modified"]
    client = TelegramClient(TEST_TOKEN, transport=httpx.MockTransport(lambda r: httpx.Response(400, json=body)))
    assert await client.edit_message_text(1, 1, "x") is None
    await client.aclose()


@pytest.mark.parametrize(
    "fixture_name",
    ["update_start.json", "update_text.json", "update_photo.json", "update_callback.json",
     "update_pre_checkout.json", "update_successful_payment.json"],
)
async def test_every_recorded_update_is_handled_without_crashing(container, fake_tg, fixture_name, caplog):
    update = load_fixture("telegram", fixture_name)
    await handle_update(container, update)
    assert "Failed to handle update" not in caplog.text


async def test_recorded_start_gets_a_reply(container, fake_tg):
    await handle_update(container, load_fixture("telegram", "update_start.json"))
    assert fake_tg.last("sendMessage").chat_id == 7001001
