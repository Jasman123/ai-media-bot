"""Per-user wizard state and server-side callback payloads, kept in Redis.

callback_data is limited to 64 bytes, so anything bigger (prompts, option sets) lives here and buttons carry a
short id.
"""

import json
import secrets
from typing import Any

from redis.asyncio import Redis

STATE_TTL_S = 3600
PAYLOAD_TTL_S = 7 * 24 * 3600


class StateStore:
    def __init__(self, redis: Redis, ttl_s: int = STATE_TTL_S):
        self.redis = redis
        self.ttl_s = ttl_s

    @staticmethod
    def _key(user_id: int) -> str:
        return f"aimb:st:{user_id}"

    async def get(self, user_id: int) -> dict[str, Any]:
        raw = await self.redis.get(self._key(user_id))
        if not raw:
            return {"step": None, "data": {}}
        return json.loads(raw)

    async def set(self, user_id: int, step: str | None, data: dict[str, Any] | None = None) -> None:
        await self.redis.set(self._key(user_id), json.dumps({"step": step, "data": data or {}}), ex=self.ttl_s)

    async def update(self, user_id: int, step: str | None = ..., **data: Any) -> dict[str, Any]:
        state = await self.get(user_id)
        if step is not ...:
            state["step"] = step
        state["data"].update(data)
        await self.redis.set(self._key(user_id), json.dumps(state), ex=self.ttl_s)
        return state

    async def clear(self, user_id: int) -> None:
        await self.redis.delete(self._key(user_id))

    async def put_payload(self, data: dict[str, Any], ttl_s: int = PAYLOAD_TTL_S) -> str:
        pid = secrets.token_urlsafe(6)
        await self.redis.set(f"aimb:pl:{pid}", json.dumps(data), ex=ttl_s)
        return pid

    async def get_payload(self, pid: str) -> dict[str, Any] | None:
        raw = await self.redis.get(f"aimb:pl:{pid}")
        return json.loads(raw) if raw else None


class UpdateDeduper:
    """Telegram retries webhook deliveries; process each update_id once."""

    TTL_S = 48 * 3600

    def __init__(self, redis: Redis):
        self.redis = redis

    async def first_seen(self, update_id: int) -> bool:
        return bool(await self.redis.set(f"aimb:upd:{update_id}", "1", nx=True, ex=self.TTL_S))

    async def forget(self, update_id: int) -> None:
        await self.redis.delete(f"aimb:upd:{update_id}")
