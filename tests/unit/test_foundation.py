import json
import logging

import httpx
import pytest

from app.config import Settings
from app.logging_setup import JsonFormatter, RedactingFormatter, configure_logging, redact
from app.telegram import ALLOWED_UPDATES, InputFile, TelegramClient, TelegramError, _clean
from tests.conftest import make_settings
from tests.support.fake_telegram import TEST_TOKEN


def test_redact_replaces_bot_token_everywhere():
    url = f"https://api.telegram.org/bot{TEST_TOKEN}/sendMessage"
    assert TEST_TOKEN not in redact(url)
    assert "<redacted-token>" in redact(url)
    assert redact("nothing secret 12:ab") == "nothing secret 12:ab"


def test_redacting_formatter_covers_tracebacks():
    formatter = RedactingFormatter("%(message)s")
    try:
        raise RuntimeError(f"boom {TEST_TOKEN}")
    except RuntimeError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed %s", (TEST_TOKEN,), sys.exc_info())
    out = formatter.format(record)
    assert TEST_TOKEN not in out
    assert out.count("<redacted-token>") >= 2


def test_json_formatter_includes_extra_fields_and_redacts():
    record = logging.LogRecord("app", logging.INFO, __file__, 1, "token %s", (TEST_TOKEN,), None)
    record.job_id = "j1"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["job_id"] == "j1"
    assert payload["level"] == "INFO"
    assert TEST_TOKEN not in payload["msg"]


def test_configure_logging_quiets_httpx():
    configure_logging("DEBUG")
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
    configure_logging("INFO")


def test_settings_defaults_keep_pending_updates():
    settings = make_settings()
    assert settings.drop_pending_updates is False  # C2
    assert isinstance(settings, Settings)


def test_allowed_updates_include_payments_and_callbacks():
    assert {"message", "callback_query", "pre_checkout_query"} <= set(ALLOWED_UPDATES)  # C1


def test_clean_drops_none_values():
    assert _clean({"a": 1, "b": None, "c": False}) == {"a": 1, "c": False}


def _client(handler) -> TelegramClient:
    return TelegramClient(TEST_TOKEN, transport=httpx.MockTransport(handler))


async def test_call_maps_api_error_with_retry_after():
    def handler(request):
        return httpx.Response(429, json={"ok": False, "error_code": 429, "description": "Too Many Requests",
                                         "parameters": {"retry_after": 3}})

    client = _client(handler)
    with pytest.raises(TelegramError) as exc:
        await client.send_message(1, "hi")
    assert exc.value.error_code == 429
    assert exc.value.retry_after == 3
    await client.aclose()


async def test_network_error_never_leaks_token():
    def handler(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}")

    client = _client(handler)
    with pytest.raises(TelegramError) as exc:
        await client.send_message(1, "hi")
    assert TEST_TOKEN not in str(exc.value)
    await client.aclose()


async def test_non_json_response_raises():
    client = _client(lambda request: httpx.Response(502, text="<html>bad gateway</html>"))
    with pytest.raises(TelegramError) as exc:
        await client.get_me()
    assert exc.value.error_code == 502
    await client.aclose()


async def test_edit_not_modified_is_swallowed():
    def handler(request):
        return httpx.Response(400, json={"ok": False, "error_code": 400,
                                         "description": "Bad Request: message is not modified"})

    client = _client(handler)
    assert await client.edit_message_text(1, 2, "same") is None
    assert await client.edit_message_reply_markup(1, 2, None) is None
    await client.aclose()


async def test_send_photo_bytes_goes_multipart_and_url_goes_json():
    seen = []

    def handler(request):
        seen.append(request.headers["content-type"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = _client(handler)
    await client.send_photo(1, InputFile("a.png", b"\x89PNG", "image/png"), caption="hi",
                            reply_markup={"inline_keyboard": []})
    await client.send_photo(1, "https://example.com/a.png")
    assert seen[0].startswith("multipart/form-data")
    assert seen[1] == "application/json"
    await client.aclose()


async def test_set_webhook_sends_allowed_updates_and_drop_flag(fake_tg):
    client = fake_tg.client()
    await client.set_webhook("https://bot.example.com/webhook/telegram", "s3cret")
    call = fake_tg.last("setWebhook")
    assert call.params["allowed_updates"] == ALLOWED_UPDATES
    assert call.params["drop_pending_updates"] is False
    assert call.params["secret_token"] == "s3cret"
    await client.aclose()


async def test_download_file_enforces_size_limit(fake_tg):
    file_id = fake_tg.add_file(b"x" * 100)
    client = fake_tg.client()
    info = await client.get_file(file_id)
    assert await client.download_file(info["file_path"]) == b"x" * 100
    with pytest.raises(TelegramError):
        await client.download_file(info["file_path"], max_bytes=10)
    await client.aclose()
