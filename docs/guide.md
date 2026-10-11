# ai-media-bot — Developer & Operator Guide

How to set up, run, test and operate the bot. The *what* and *why* live in [plan.md](../plan.md); the
technical reference (modules, data model, flows, APIs) is [specification.md](specification.md).

## 1. Prerequisites

- Python 3.10+ (Docker image uses 3.12)
- Docker (for the test Postgres/Redis and for deployment)

## 2. Local setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env          # fill in BOT_TOKEN, WEBHOOK_SECRET, API_KEY
```

Run the API (module-level app; config is read at startup, not import):

```bash
.venv/bin/uvicorn app.main:app --reload
```

## 3. Configuration

All settings come from environment variables (or `.env`), see [app/config.py](../app/config.py).

| Variable | Default | Notes |
|---|---|---|
| `BOT_TOKEN` | — | From @BotFather. Never logged: the logging layer redacts tokens (C7). |
| `BOT_USERNAME` | `""` | Used for deep links and watermarks. |
| `WEBHOOK_URL` | `""` | Public base URL; `/webhook/telegram` is appended. Empty = don't register. |
| `WEBHOOK_SECRET` | — | Checked in constant time on every webhook call. |
| `API_KEY` | — | `X-API-Key` for `/api/*` admin endpoints. |
| `DROP_PENDING_UPDATES` | `false` | Keep `false` in prod (C2): dropping queued updates can lose `successful_payment`. |
| `SET_WEBHOOK_ON_STARTUP` | `true` | |
| `TELEGRAM_TIMEOUT_S` | `60` | Shared httpx client timeout (C4). |
| `LOG_LEVEL` / `LOG_JSON` | `INFO` / `false` | JSON logs for production. `httpx` is pinned to WARNING (C7). |

## 4. Testing

Four layers, selected by directory and marker:

| Layer | Dir | Marker | Needs |
|---|---|---|---|
| Unit | `tests/unit` | `-m unit` | nothing |
| Fixture | `tests/fixture` | `-m fixture` | recorded payloads in `tests/fixtures/` |
| Integration | `tests/integration` | `-m integration` | test Postgres + Redis, real sockets |
| End-to-end | `tests/e2e` | `-m e2e` | full stack: webhook → queue → worker → fake Telegram/provider |

```bash
scripts/test.sh                 # starts test services, lints, runs everything
scripts/test.sh -m unit         # one layer
scripts/test-services.sh down   # remove the test containers
```

The test services run in throwaway containers on **55432** (Postgres) and **56379** (Redis), so they never touch
other local services. Override with `TEST_DATABASE_URL` / `TEST_REDIS_URL`.

Test helpers in `tests/support/`:
- `fake_telegram.py` — in-memory Bot API (records calls, parses multipart, serves files).
- `updates.py` — builders for real-shaped Telegram updates.
- `live_server.py` — serves an ASGI app on a real localhost port.

## 5. Branching workflow

- `epic/v1` is the integration branch for the build. Each feature gets `feat/<id>-<slug>` from `epic/v1`.
- A feature merges back (`--no-ff`) only when `scripts/test.sh` is green (lint + all four layers).
- After each merge, this guide and the specification are updated.

## 6. Changelog (merged features)

| Branch | Plan items | Summary |
|---|---|---|
| `feat/p0-foundation` | C1, C2, C4, C7, C8 | Shared Telegram client (multipart, keyboards-ready, errors without token), `pre_checkout_query` in allowed updates, no update dropping by default, token-redacting logs, app factory + DI container, four-layer test harness. |
