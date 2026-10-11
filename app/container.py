"""Process-wide dependencies, built once and shared by the API and the worker.

Tests build a Container with fakes instead of patching module globals.
"""

from dataclasses import dataclass

from .config import Settings
from .telegram import TelegramClient


@dataclass
class Container:
    settings: Settings
    telegram: TelegramClient

    async def aclose(self) -> None:
        await self.telegram.aclose()


def build_container(settings: Settings, **overrides) -> Container:
    telegram = overrides.pop("telegram", None) or TelegramClient(
        settings.bot_token, settings.telegram_api_base, settings.telegram_timeout_s
    )
    return Container(settings=settings, telegram=telegram, **overrides)
