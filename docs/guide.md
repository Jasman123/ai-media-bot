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

Run the stack (Postgres + Redis must be reachable via `DATABASE_URL` / `REDIS_URL`):

```bash
.venv/bin/alembic upgrade head                 # create / migrate tables
.venv/bin/uvicorn app.main:app --reload        # API + webhook (config is read at startup, not import)
.venv/bin/python -m app.worker updates         # processes Telegram updates
.venv/bin/python -m app.worker jobs            # generation, delivery and cron work
```

The webhook only validates, dedupes and enqueues; **nothing is handled unless an `updates` worker runs**.

### Migrations

- One Alembic revision per feature in `alembic/versions/` (`0001_users`, ...).
- `tests/integration/test_db_queue.py::test_migrations_match_models` fails if models and migrations drift.
- New revision: edit models, then `alembic revision --autogenerate -m "..."` against a dev DB and review it.

## 3. Where code goes (adding a feature)

Follow the layers in [specification.md §1](specification.md#1-architecture); `tests/unit/test_architecture.py`
fails the build on a wrong import.

1. **Domain** (`app/domain/<area>.py`): entities as frozen dataclasses, domain errors, and a `Protocol` for each
   repository or external system the feature needs.
2. **Infra** (`app/infra/`): ORM table in `models/`, SQL repository in `repositories/` returning domain entities,
   other adapters (HTTP clients, storage) implementing the ports. Add the Alembic revision.
3. **Service** (`app/services/<area>.py`): the use case. All collaborators via `__init__`, typed as ports. Commit
   through the injected `UnitOfWork`.
4. **Wire it** in `app/di.py`: `provide(SqlThingRepository, provides=ThingRepository)` in the REQUEST scope,
   `provide(ThingService, scope=Scope.REQUEST)`.
5. **Interface**: a handler module in `app/bot/handlers/` with its own `Router`, included in `build_router()`.
   Handlers take services as typed parameters: `async def handler(ctx: Ctx, things: ThingService)`.
   Screens (text + keyboard) go in `app/bot/views.py`; strings in both `app/i18n/*.yaml`.
6. **Tests**: unit tests construct the service with fakes from `tests/support/fakes.py`; integration tests hit
   the real repository; e2e tests drive the bot through `stack.user(...)`.

## 4. Configuration

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
| `DATABASE_URL` | `postgresql+asyncpg://aimb:aimb@localhost:5432/aimb` | Must use the `asyncpg` driver. |
| `REDIS_URL` | `redis://localhost:6379/0` | ARQ queues, wizard state, update dedupe. |
| `DB_POOL_SIZE` | `10` | |
| `LOG_LEVEL` / `LOG_JSON` | `INFO` / `false` | JSON logs for production. `httpx` is pinned to WARNING (C7). |
| `STARS_NET_USD` / `MARKUP` / `MIN_MARGIN` | `0.013` / `3` / `1.5` | Pricing policy (plan §5). Change, then `POST /api/admin/catalog/reprice`. |
| `ENABLE_FAKE_PROVIDER` | `false` | Zero-spend FakeProvider for dev/CI. **Never true in production.** |
| `FAKE_PROVIDER_DELAY_POLLS` | `2` | Status polls before a `fake/delayed` job completes. |
| `CATALOG_SEED_PATH` | `""` | Alternative catalog YAML; default `app/infra/seed/catalog.yaml`. |

### Operating the catalog

```bash
H="X-API-Key: $API_KEY"
curl -H "$H" localhost:8000/api/admin/catalog                                   # what's offered
curl -H "$H" -X PATCH localhost:8000/api/admin/catalog/img-quality-fake \
     -H 'content-type: application/json' -d '{"enabled": false}'                # take a model out
curl -H "$H" -X POST localhost:8000/api/admin/providers/fal/disable              # kill a whole provider
curl -H "$H" -X POST localhost:8000/api/admin/catalog/reprice                    # after changing MARKUP
```

## 5. Testing

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
- `live_server.py` — serves an ASGI app (or the fake Bot API) on a real localhost port.
- `fakes.py` — in-memory implementations of every domain port, for service unit tests.
- `di.py` — `make_test_container(settings, telegram, *providers, engine=, redis=)` and `ValueProvider({Port: fake})`
  to override any binding while keeping the production wiring for everything else.
- `stack.py` — `stack` fixture: real app + Postgres + Redis/ARQ + fake Telegram. `stack.user(id)` gives a
  `BotUser` with `send(text)`, `press(callback_data)`, `press_button(text)`, `photo()`; each call posts a
  webhook update and drains the queues with burst workers (`drain()` raises if any job failed).

Each test session creates its own database (`aimb_test_<random>`) and migrates it with Alembic; every
integration/e2e test starts from truncated tables and an empty Redis db.

## 6. Branching workflow

- `epic/v1` is the integration branch for the build. Each feature gets `feat/<id>-<slug>` from `epic/v1`.
- A feature merges back (`--no-ff`) only when `scripts/test.sh` is green (lint + all four layers).
- After each merge, this guide and the specification are updated.

## 7. Changelog (merged features)

| Branch | Plan items | Summary |
|---|---|---|
| `feat/p0-foundation` | C1, C2, C4, C7, C8 | Shared Telegram client (multipart, keyboards-ready, errors without token), `pre_checkout_query` in allowed updates, no update dropping by default, token-redacting logs, app factory + DI container, four-layer test harness. |
| `feat/p0-db-router-i18n` | C3, C5, C6, F15 | Postgres + Alembic, user model, ARQ `updates` queue behind the webhook (dedupe, 503 on queue failure), router (commands / callbacks / wizard states / media / payments), ID+EN catalogs with language picker, worker entrypoint, subprocess smoke tests. |
| `refactor/p0-layers-di` | (structure) | Layered architecture (domain / services / infra / bot+api) with dishka DI: typed handler dependencies, APP/REQUEST scopes, composition root, architecture test. Review fixes: secret checked before DI resolution, fail-fast startup, no update loss on partial Redis failure, graceful worker shutdown. |
| `feat/a1-a3-provider-framework` | A1, A2, A3 | `MediaProvider` port + error taxonomy, provider registry, DB model catalog (seeded from YAML, admin-editable), tier → primary → fallback resolution, plan §5 pricing + margin floor, FakeProvider with 8 modes, provider contract suite, admin catalog API. |
| `feat/m1-credit-ledger` | M1 | Append-only ledger (DB trigger), free/paid buckets, reserve → capture/release with account row locks, idempotent grants, clamped debits, FIFO free-credit expiry + daily cron, Credits screen and `/saldo`. Ledger scenarios are JSON fixtures run on both in-memory and Postgres repos. |
