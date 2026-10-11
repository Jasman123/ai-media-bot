"""Filesystem storage with HMAC-signed, expiring URLs served by app/api/media.py (dev and single-box deploys)."""

import asyncio
import hashlib
import hmac
import mimetypes
import time
from pathlib import Path
from urllib.parse import quote


def sign(secret: bytes, key: str, expires: int) -> str:
    return hmac.new(secret, f"{key}|{expires}".encode(), hashlib.sha256).hexdigest()


def verify(secret: bytes, key: str, expires: int, signature: str, now: float | None = None) -> bool:
    if expires < (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(sign(secret, key, expires), signature)


class LocalStorage:
    def __init__(self, root: Path, public_base_url: str, secret: bytes):
        self._root = root.resolve()
        self._base = public_base_url.rstrip("/")
        self._secret = secret

    def path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if self._root not in path.parents:
            raise ValueError(f"storage key escapes root: {key!r}")
        return path

    async def put(self, key: str, data: bytes, mime: str) -> None:
        path = self.path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)

        await asyncio.to_thread(write)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self.path(key).read_bytes)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.path(key).unlink, True)

    async def url(self, key: str, expires_s: int = 3600) -> str:
        expires = int(time.time()) + expires_s
        return f"{self._base}/media/{quote(key)}?exp={expires}&sig={sign(self._secret, key, expires)}"

    async def read_signed(self, key: str, expires: int, signature: str) -> tuple[bytes, str] | None:
        if not verify(self._secret, key, expires, signature):
            return None
        try:
            data = await self.get(key)
        except (FileNotFoundError, ValueError):
            return None
        return data, mimetypes.guess_type(key)[0] or "application/octet-stream"


class NoSignedMedia:
    """For object stores that hand out their own presigned URLs (S3/R2)."""

    async def read_signed(self, key: str, expires: int, signature: str) -> tuple[bytes, str] | None:
        return None
