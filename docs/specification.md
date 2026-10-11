# ai-media-bot — Technical Specification

Living reference for what is implemented. Product decisions and rationale: [plan.md](../plan.md).
How to run and test: [guide.md](guide.md).

## 1. Architecture

### 1.1 Layers

```
            ┌──────────────────────── interfaces ────────────────────────┐
            │  app/bot  (Telegram: router, dispatcher, handlers, views)   │
            │  app/api  (FastAPI: webhook, admin)                          │
            └───────────────┬─────────────────────────────────────────────┘
                            │ calls (injected by type)
            ┌───────────────▼──────────────┐
            │  app/services  (use cases)   │  owns transactions (UnitOfWork.commit)
            └───────────────┬──────────────┘
                            │ depends on ports only
            ┌───────────────▼──────────────┐        implements        ┌──────────────────────────────┐
            │  app/domain  (entities,      │ ◄──────────────────────── │  app/infra  (SQL repos, ORM, │
            │  errors, ports = Protocols)  │                           │  Redis, ARQ, Telegram client)│
            └──────────────────────────────┘                           └──────────────────────────────┘
                     app/di.py = composition root (binds ports → adapters); main.py / worker.py = entrypoints
```

| Layer | May import | Must not import |
|---|---|---|
| `app.domain` | `app.domain` | any framework, any other layer |
| `app.services` | `app.domain`, `app.services` | infra, bot, api, frameworks |
| `app.infra` | `app.domain`, `app.infra` | services, bot, api, FastAPI, dishka |
| `app.i18n` | `app.domain` | frameworks |
| `app.bot` | domain, services, i18n, `app.infra.telegram`, dishka | repositories, ORM, SQLAlchemy, Redis |
| `app.api` | domain, services, config, `app.infra.telegram` | repositories, ORM, SQLAlchemy, Redis |

Enforced by `tests/unit/test_architecture.py` (AST import scan) on every test run.

Rules of thumb:
- **Handlers and endpoints are thin**: parse input → call one service → render. No SQL, no business rules.
- **Services** take every collaborator through `__init__`, typed against a domain port, and decide when to commit.
- **Repositories** never commit and return domain entities; ORM rows (`*Row`) never leave `app.infra`.
- **Configuration** reaches infra/services as constructor arguments built in `app/di.py`; they don't read settings.

### 1.2 Dependency injection (`app/di.py`, dishka)

| Scope | Lifetime | Provides |
|---|---|---|
| `APP` | process | `Settings`, `AsyncEngine`, `async_sessionmaker`, `ArqRedis`, `TelegramClient`, `I18n`, `Clock`, `TaskQueue`, `WizardState`, `UpdateDeduper`, `UpdateIntake`, `ProviderRegistry` (+ adapters), `PricingPolicy`, `CatalogSeed`, `Router`, `BotConfig` |
| `REQUEST` | one HTTP request or one ARQ job | `AsyncSession`, `UnitOfWork`, repositories, `UserService`, `CatalogService`, `LedgerService`, `Dispatcher` |

- **Bot handlers** declare dependencies as typed parameters; the `Dispatcher` resolves them from the job's REQUEST
  container. All objects in one update share one DB session:
  ```python
  @router.callback("lang")
  async def choose_language(ctx: Ctx, users: UserService) -> None:
      ctx.with_user(await users.choose_language(ctx.user.id, ctx.args[0]))
  ```
- **FastAPI**: `setup_dishka` middleware opens a REQUEST scope per request; endpoints take `FromDishka[T]`.
- **ARQ**: `setup_dishka(container, worker)` opens/closes a REQUEST scope per job; tasks use `@inject` +
  `FromDishka[T]`.
- `make_container()` validates the whole graph at import; `warm_up()` connects Postgres/Redis at startup so a
  missing dependency fails the process, not a request.
- **Tests** call `make_container(*overrides)`; later providers override earlier ones
  (`tests/support/di.py: TestOverrides, ValueProvider`). Unit tests skip DI and construct services with the
  in-memory fakes in `tests/support/fakes.py`.

### 1.3 Runtime

```
Telegram ──webhook──► FastAPI ──UpdateIntake──► ARQ "aimb:updates" ──► worker: Dispatcher ──► handler(ctx, services…)
                                                                                                │
                                                                         Postgres ◄── repositories
                                                                         Redis    ◄── wizard state
```

Queues (`app/domain/tasks.py`): `aimb:updates` (Telegram updates, user-facing) and `aimb:jobs` (generation,
delivery, cron). One worker process per queue: `python -m app.worker updates|jobs`.

## 2. Update pipeline

1. `POST /webhook/telegram`: the secret is checked in a dependency **before** any DI resolution (403);
   the body needs an integer `update_id` (400).
2. `UpdateIntake.accept`: `UpdateDeduper.seen(id)` → if new, enqueue `process_update(update)` with job id
   `upd:<update_id>` (ARQ collapses concurrent duplicates) → only then `mark(id)` (48 h TTL). Duplicates answer
   `{"ok": true, "duplicate": true}`.
3. Any Redis/queue failure before the enqueue succeeds → **503**, and no marker is left behind, so Telegram's
   redelivery is processed (C3). A failing `mark()` after a successful enqueue is only logged.
4. The worker's `Dispatcher` registers the sender via `UserService.register_seen` (race-safe
   `INSERT … ON CONFLICT`) and routes.
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

### Wizard state (port `WizardState`, adapter `app/infra/state.py`)

- `RedisWizardState`: `aimb:st:<user_id>` → `{"step": str|null, "data": {...}}`, 1 h TTL.
- `put_payload(dict) -> id` / `get_payload(id)`: server-side payloads for buttons (`callback_data` ≤ 64 bytes),
  7-day TTL.
- `keyboards.cb(*parts)` builds callback data and raises if it exceeds 64 bytes.

### i18n (`app/i18n/`)

- `id.yaml` (default, D1/F15) and `en.yaml`, dotted keys, `str.format` placeholders.
- Fallback: requested language → Indonesian → the key itself.
- Tests enforce: same key set in both, same placeholders per key, every key used in code exists, balanced HTML.

### Commands

`/start` (language picker on first use, then menu), `/menu`, `/saldo` (`/kredit`, `/balance`, `/credits`),
`/help` (`/bantuan`), `/settings`
(`/pengaturan`), `/language` (`/bahasa`), `/cancel` (`/batal`). `setMyCommands` runs per language at startup
when the webhook is registered.

## 3. Data model

| Table | Columns (key ones) | Notes |
|---|---|---|
| `users` (`UserRow` → domain `User`) | `id`, `telegram_id` (unique), `kind` (`user`/`group`), `username?`, `language` (`id` default), `language_chosen`, `is_banned`, `ban_reason?`, `created_at`, `last_seen_at` | PDP-minimal: no names or phone numbers |

### 3.1 Model catalog (`model_catalog`, A2)

| Column | Meaning |
|---|---|
| `id` | catalog key, e.g. `img-fast-fake` |
| `kind`, `tier` | `image`/`video`; tier the user picks (`fast`, `quality`, `pro`, `hemat`, `tool`, …) |
| `provider`, `provider_model_id` | registry name + vendor model id (fake: `fake/<mode>`) |
| `capabilities` | operations served: `text2img img2img text2vid img2vid frames2vid video_extend upscale remove_bg restore` |
| `cost_model`, `unit_cost_usd`, `audio_unit_cost_usd` | `per_image` / `per_megapixel` / `per_second` / `per_job` |
| `credits`, `audio_credits`, `credits_override` | user price per unit; override = admin-pinned, repricing skips it |
| `durations`, `aspect_ratios`, `resolutions`, `max_n`, `eta_s` | what the wizard may offer; ETA for status messages |
| `priority`, `fallback_id`, `enabled`, `notes` | lower priority wins within a tier; fallback chain; kill switch |

- **Seed**: `app/infra/seed/catalog.yaml`, inserted at startup for missing ids only (admin edits survive deploys).
  Rows without `credits` are priced by the policy.
- **Pricing** (`domain/catalog.py`): `credits = ceil(unit_cost × markup ÷ stars_net_usd)` per unit
  (defaults 3× and $0.013). A request costs `credits × units` (n images, seconds, or 1 job). The price is
  attached to the **tier's primary model**; a fallback may serve it only if
  `credits × 0.013 ≥ min_margin (1.5) × fallback cost`.
- **Resolution** (`CatalogService`): `tiers(op)` = primary (lowest priority) usable model per tier, cheapest tier
  first; usable = enabled + provider registered + supports op. `chain(model_id, op)` follows `fallback_id`,
  skipping unusable rows, cycle-safe.

### 3.2 Credit ledger (M1)

| Table | Columns | Notes |
|---|---|---|
| `ledger_entries` | `id`, `user_id`, `bucket` (`free`/`paid`), `amount` (±), `kind`, `reservation_id?`, `job_id?`, `payment_id?`, `expires_at?`, `note?`, `idempotency_key?` (unique), `created_at` | **Append-only**: a Postgres trigger rejects `UPDATE`/`DELETE` |
| `credit_reservations` | `id` (uuid), `user_id`, `free_amount`, `paid_amount`, `status` (`held`/`captured`/`released`), `job_id?`, `created_at`, `settled_at?` | |

- **Balance** = `SUM(amount)` per bucket. Entry kinds: `signup_gift, purchase, subscription, reserve, capture,
  release, admin_grant, admin_revoke, referral_bonus, referral_reward, expiry, stars_refund, transfer_in/out`.
- **Reserve**: lock the account row (`SELECT … FOR UPDATE` on `users`), apply due expiry, split the amount
  (free first, free only if the job is an image — D5), write the reservation + negative `reserve` rows. Concurrent
  reserves serialise on the lock, so the balance can't go negative.
- **Capture** (job delivered) writes zero-amount `capture` audit rows; **release** (failure/timeout/moderation)
  writes positive `release` rows. Both are idempotent; capture-after-release raises `ReservationConflict`.
- **Grant** with an `idempotency_key` is credited at most once (used for `pay:<telegram_payment_charge_id>`).
  Paid credits can't carry an expiry.
- **Debit** (admin revoke, Stars refund) is clamped at the bucket balance and returns the amount actually taken.
- **Free expiry (FIFO)**: expired = `max(0, free_balance − Σ unexpired free grants)` (release rows don't count as
  grants). Applied lazily on reserve/summary and by the daily cron `expire_free_credits` (17:05 UTC = 00:05 WIB).
- Services accept `commit=False` so a caller can compose a ledger change with its own writes in one transaction.

## 4. Provider adapters (A1, A3)

- Port `MediaProvider` (`domain/generation.py`): `submit`, `status`, `parse_webhook` (verifies signature, raises
  `InvalidInput` if bad), `cancel`, `fetch` (download result before URLs expire).
- Normalised types: `GenerationRequest(operation, provider_model_id, prompt, input_urls, aspect_ratio,
  duration_s, resolution, audio, n, seed, webhook_url)`, `ProviderStatus(state, queue_position, result, error)`.
- Error taxonomy → core behaviour: `Retryable` (retry later) · `ProviderDown` (breaker + fallback) ·
  `ModerationBlocked` (refund, plain message) · `InvalidInput` (refund, message) · `QuotaExhausted`
  (alert, pause model, fallback).
- `ProviderRegistry` is built in `app/di.py::ProviderAdapters`; only `app/infra/providers/*` know vendor details.
- **FakeProvider** (`ENABLE_FAKE_PROVIDER=true`, dev/CI only): modes via `fake/<mode>` or a `[[mode]]` prompt
  marker — `instant`, `delayed` (N polls), `fail`, `moderation`, `down`, `quota`, `invalid`, `retry`. Returns real
  PNGs (sized by aspect ratio) and a stub MP4 as `data:` URLs; webhooks are HMAC-signed (`X-Fake-Signature`).
- **Contract suite** `tests/support/provider_contract.py`: every adapter subclass must pass submit→poll→fetch,
  failure and moderation normalisation, signed/failed/tampered webhooks, idempotent cancel.
- **Adding a provider**: adapter module in `app/infra/providers/`, one line in `ProviderAdapters`, catalog rows,
  a `Test<Vendor>Contract` subclass with recorded fixtures. No other file changes.

## 4b. Telegram client (`app/infra/telegram.py`)

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
| GET | `/api/admin/catalog` | `X-API-Key` | registered providers + all catalog rows |
| PATCH | `/api/admin/catalog/{id}` | `X-API-Key` | edit `enabled, priority, credits, audio_credits, credits_override, unit_cost_usd, audio_unit_cost_usd, fallback_id, provider, provider_model_id, eta_s, notes`; setting credits pins them |
| POST | `/api/admin/providers/{name}/enable\|disable` | `X-API-Key` | kill switch for every row of a provider |
| POST | `/api/admin/catalog/reprice` | `X-API-Key` | recompute credits from costs (overrides kept) |

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
