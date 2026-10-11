# ai-media-bot — Technical Specification

Living reference for what is implemented. Product decisions and rationale: [plan.md](../plan.md).
How to run and test: [guide.md](guide.md).

## 1. Architecture

```
Telegram ──webhook──► FastAPI (app/main.py: create_app)
                          │
                          └─► handlers (in-process for now; queue arrives with the router feature)
```

- **App factory** `app.main.create_app(container=None)`. With no container, the lifespan builds one from
  settings at startup (so importing `app.main` never needs env vars). Tests pass a prebuilt container.
- **Container** (`app/container.py`): process-wide dependencies shared by API and worker. Grows feature by
  feature; tests swap in fakes instead of patching globals.

## 2. Telegram client (`app/telegram.py`)

- One shared `httpx.AsyncClient` per process, 60 s timeout, pooled connections (C4).
- `call()` for JSON methods; `call_multipart()` for uploads (`InputFile`); dict/list params are JSON-encoded.
- Errors raise `TelegramError(method, error_code, description, retry_after)`. Network errors report only the
  exception type, never the URL, because the URL contains the bot token (C7).
- `editMessageText` / `editMessageReplyMarkup` treat "message is not modified" as success.
- `download_file()` streams with a size cap (default 20 MB, the cloud Bot API download limit).
- `ALLOWED_UPDATES` = `message, callback_query, pre_checkout_query, inline_query, my_chat_member` (C1).
- `set_webhook(..., drop_pending_updates=False)` by default (C2).

## 3. HTTP endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/webhook/telegram` | `X-Telegram-Bot-Api-Secret-Token` (constant-time) | Telegram updates |
| GET | `/api/health` | none | Liveness |
| POST | `/api/send` | `X-API-Key` | Send a plain message |
| GET | `/api/webhook-info` | `X-API-Key` | `getWebhookInfo` passthrough |

## 4. Logging (`app/logging_setup.py`)

- `configure_logging(level, json)`; `httpx`, `httpcore`, `botocore` pinned to WARNING.
- Every formatted record (tracebacks included) passes through `redact()`, which masks anything shaped like a
  bot token.
- JSON mode emits `ts, level, logger, msg` plus `job_id, user_id, update_id, provider` when set via `extra=`.

## 5. Issue tracker from plan §1

| # | Status |
|---|---|
| C1 `pre_checkout_query` missing | Fixed: in `ALLOWED_UPDATES` |
| C2 `drop_pending_updates=True` | Fixed: setting, default `false` |
| C3 BackgroundTasks | Open: replaced by the ARQ queue in `feat/p0-router-i18n` |
| C4 client per call | Fixed: shared client, multipart |
| C5 message-only handler | Open: router in `feat/p0-router-i18n` |
| C6 no DB | Open: `feat/p0-router-i18n` |
| C7 token in logs | Fixed: httpx at WARNING + redaction |
| C8 `cloudflared.deb` | Fixed: `*.deb` gitignored |
