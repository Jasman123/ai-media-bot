import secrets

from fastapi import Header, HTTPException, Request

from .container import Container


def get_container(request: Request) -> Container:
    return request.app.state.container


def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    expected = get_container(request).settings.api_key
    if not secrets.compare_digest((x_api_key or "").encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid API key")
