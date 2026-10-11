from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"

    # Telegram
    bot_token: str
    bot_username: str = ""
    webhook_url: str = ""
    webhook_secret: str
    telegram_api_base: str = "https://api.telegram.org"
    telegram_timeout_s: float = 60.0
    # C2: never drop queued updates in prod; a dropped successful_payment means a paid user without credits.
    drop_pending_updates: bool = False
    set_webhook_on_startup: bool = True

    # Admin / REST API
    api_key: str

    # Storage of state
    database_url: str = "postgresql+asyncpg://aimb:aimb@localhost:5432/aimb"
    redis_url: str = "redis://localhost:6379/0"
    db_pool_size: int = 10

    # Logging
    log_level: str = "INFO"
    log_json: bool = False

    # Pricing (plan §5): stars = ceil(provider_cost_usd × markup ÷ stars_net_usd)
    stars_net_usd: Decimal = Decimal("0.013")
    markup: Decimal = Decimal("3")
    min_margin: Decimal = Decimal("1.5")

    # Providers
    enable_fake_provider: bool = False   # dev/CI only: zero-spend FakeProvider (A3)
    fake_provider_delay_polls: int = 2
    fake_provider_webhook_secret: str = "fake-webhook-secret"  # noqa: S105 - dev-only fake, not a credential
    catalog_seed_path: str = ""          # empty = app/infra/seed/catalog.yaml

    # Jobs
    job_image_timeout_s: int = 180
    job_video_timeout_s: int = 900
    job_image_poll_s: float = 2.0
    job_video_poll_s: float = 10.0
    job_max_submit_retries: int = 3
    job_stuck_after_s: int = 120

    # Storage: "local" (disk + signed /media URLs) or "s3" (Cloudflare R2 / any S3)
    public_base_url: str = ""            # defaults to WEBHOOK_URL; used for provider webhooks and media links
    storage_backend: Literal["local", "s3"] = "local"
    storage_dir: str = "media"
    media_signing_key: str = ""          # defaults to WEBHOOK_SECRET
    s3_endpoint_url: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = ""
    s3_region: str = "auto"

    @property
    def base_url(self) -> str:
        return (self.public_base_url or self.webhook_url).rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
