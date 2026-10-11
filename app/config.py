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


@lru_cache
def get_settings() -> Settings:
    return Settings()
