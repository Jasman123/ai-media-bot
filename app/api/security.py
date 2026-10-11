import secrets

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Header, HTTPException

from ..config import Settings


def constant_time_equals(provided: str | None, expected: str) -> bool:
    return secrets.compare_digest((provided or "").encode(), expected.encode())


@inject
async def require_api_key(settings: FromDishka[Settings], x_api_key: str | None = Header(default=None)) -> None:
    if not constant_time_equals(x_api_key, settings.api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
