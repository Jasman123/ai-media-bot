# ai-media-bot — Technical Specification

Living reference for what is implemented. Product decisions and rationale: [plan.md](../plan.md).
How to run and test: [guide.md](guide.md).

## 1. Architecture

```
Telegram ──webhook──► FastAPI ──dedupe (Redis)──► ARQ queue "aimb:updates" ──► worker: router.dispatch
                                                                                   │
                                                              Postgres (users) ◄───┤
                                                              Redis (wizard state)◄┘
```

Queues (`app/queue.py`): `aimb:updates` (Telegram updates, user-facing) and `aimb:jobs` (generation, delivery,
cron). One worker process per queue: `python -m app.worker updates|jobs`.

- **App factory** `app.main.create_app(container=None)`. With no container, the lifespan builds one from
  settings at startup (so importing `app.main` never needs env vars). Tests pass a prebuilt container.
- **Container** (`app/container.py`): process-wide dependencies shared by API and worker. Grows feature by
  feature; tests swap in fakes instead of patching globals.

## 2. Update pipeline

1. `POST /webhook/telegram` checks the secret (403), requires an integer `update_id` (400).
2. `UpdateDeduper.first_seen(update_id)` — Redis `SET NX` with 48 h TTL. Duplicates answer
   `{"ok": true, "duplicate": true}`.
3. Enqueue `process_update(update)` with job id `upd:<update_id>`. If enqueueing fails, the dedupe key is
   released and the endpoint answers **503**, so Telegram redelivers instead of the update being lost (C3).
4. The worker upserts the sender (`get_or_create_user`, race-safe `INSERT … ON CONFLICT`) and routes.
   `TelegramError` with `retry_after` → ARQ `Retry` (max 3 tries). Other handler exceptions are logged and the
   user gets `errors.generic`.

### Router (`app/bot/router.py`)

| Update | Routed to |
|---|---|
| `pre_checkout_query` | event `pre_checkout` |
| `inline_query` | event `inline_query` (not for banned users) |
| `my_chat_member` | event `my_chat_member` |
| `callback_query` with data `ns:arg1:arg2` | `callbacks[ns]` with `ctx.args = [arg1, arg2]`, else `unknown_callback` |
| message with `successful_payment` / `refunded_payment` | those events — **even for banned users** (money is never dropped) |
| `/cmd[@bot] args` | `commands[cmd]`, else `unknown_command`; commands for other bots are ignored |
| non-command in a group | ignored |
| photo / image document, voice, video | events `photo`, `voice`, `video` |
| text | `states[<wizard step>]` if the user's state has a registered step, else event `text` |

Banned users: callbacks get an alert, private messages get `errors.banned`, nothing else runs.

### Wizard state (`app/state.py`)

- `StateStore`: `aimb:st:<user_id>` → `{"step": str|null, "data": {...}}`, 1 h TTL.
- `put_payload(dict) -> id` / `get_payload(id)`: server-side payloads for buttons (`callback_data` ≤ 64 bytes),
  7-day TTL.
- `keyboards.cb(*parts)` builds callback data and raises if it exceeds 64 bytes.

### i18n (`app/i18n/`)

- `id.yaml` (default, D1/F15) and `en.yaml`, dotted keys, `str.format` placeholders.
- Fallback: requested language → Indonesian → the key itself.
- Tests enforce: same key set in both, same placeholders per key, every key used in code exists, balanced HTML.

### Commands

`/start` (language picker on first use, then menu), `/menu`, `/help` (`/bantuan`), `/settings`
(`/pengaturan`), `/language` (`/bahasa`), `/cancel` (`/batal`). `setMyCommands` runs per language at startup
when the webhook is registered.

## 3. Data model

| Table | Columns (key ones) | Notes |
|---|---|---|
| `users` | `id`, `telegram_id` (unique), `kind` (`user`/`group`), `username?`, `language` (`id` default), `language_chosen`, `is_banned`, `ban_reason?`, `created_at`, `last_seen_at` | PDP-minimal: no names or phone numbers |

## 4. Telegram client (`app/telegram.py`)

- One shared `httpx.AsyncClient` per process, 60 s timeout, pooled connections (C4).
- `call()` for JSON methods; `call_multipart()` for uploads (`InputFile`); dict/list params are JSON-encoded.
- Errors raise `TelegramError(method, error_code, description, retry_after)`. Network errors report only the
  exception type, never the URL, because the URL contains the bot token (C7).
- `editMessageText` / `editMessageReplyMarkup` treat "message is not modified" as success.
- `download_file()` streams with a size cap (default 20 MB, the cloud Bot API download limit).
- `ALLOWED_UPDATES` = `message, callback_query, pre_checkout_query, inline_query, my_chat_member` (C1).
- `set_webhook(..., drop_pending_updates=False)` by default (C2).

## 5. HTTP endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/webhook/telegram` | `X-Telegram-Bot-Api-Secret-Token` (constant-time) | Telegram updates |
| GET | `/api/health` | none | Liveness |
| POST | `/api/send` | `X-API-Key` | Send a plain message |
| GET | `/api/webhook-info` | `X-API-Key` | `getWebhookInfo` passthrough |

## 6. Logging (`app/logging_setup.py`)

- `configure_logging(level, json)`; `httpx`, `httpcore`, `botocore` pinned to WARNING.
- Every formatted record (tracebacks included) passes through `redact()`, which masks anything shaped like a
  bot token.
- JSON mode emits `ts, level, logger, msg` plus `job_id, user_id, update_id, provider` when set via `extra=`.

## 7. Issue tracker from plan §1

| # | Status |
|---|---|
| C1 `pre_checkout_query` missing | Fixed: in `ALLOWED_UPDATES` |
| C2 `drop_pending_updates=True` | Fixed: setting, default `false` |
| C3 BackgroundTasks | Fixed: ARQ `updates` queue, 503 on enqueue failure |
| C4 client per call | Fixed: shared client, multipart |
| C5 message-only handler | Fixed: `app/bot/router.py` |
| C6 no DB | Fixed: Postgres + Alembic; update-id dedupe in Redis |
| C7 token in logs | Fixed: httpx at WARNING + redaction |
| C8 `cloudflared.deb` | Fixed: `*.deb` gitignored |
