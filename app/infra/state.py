"""Redis adapters for the WizardState and UpdateDeduper ports."""

import json
import secrets
from typing import Any

from arq.connections import ArqRedis

STATE_TTL_S = 3600
PAYLOAD_TTL_S = 7 * 24 * 3600
DEDUPE_TTL_S = 48 * 3600


class RedisWizardState:
    def __init__(self, redis: ArqRedis, ttl_s: int = STATE_TTL_S):
        self._redis = redis
        self._ttl_s = ttl_s

    @staticmethod
    def _key(user_id: int) -> str:
        return f"aimb:st:{user_id}"

    async def get(self, user_id: int) -> dict[str, Any]:
        raw = await self._redis.get(self._key(user_id))
        if not raw:
            return {"step": None, "data": {}}
        return json.loads(raw)

    async def set(self, user_id: int, step: str | None, data: dict[str, Any] | None = None) -> None:
        await self._redis.set(self._key(user_id), json.dumps({"step": step, "data": data or {}}), ex=self._ttl_s)

    async def update(self, user_id: int, step: str | None = ..., **data: Any) -> dict[str, Any]:
        state = await self.get(user_id)
        if step is not ...:
            state["step"] = step
        state["data"].update(data)
        await self._redis.set(self._key(user_id), json.dumps(state), ex=self._ttl_s)
        return state

    async def clear(self, user_id: int) -> None:
        await self._redis.delete(self._key(user_id))

    async def put_payload(self, data: dict[str, Any], ttl_s: int = PAYLOAD_TTL_S) -> str:
        pid = secrets.token_urlsafe(6)
        await self._redis.set(f"aimb:pl:{pid}", json.dumps(data), ex=ttl_s)
        return pid

    async def get_payload(self, pid: str) -> dict[str, Any] | None:
        raw = await self._redis.get(f"aimb:pl:{pid}")
        return json.loads(raw) if raw else None


class RedisUpdateDeduper:
    """Telegram retries webhook deliveries; remember accepted update_ids for 48 h."""

    def __init__(self, redis: ArqRedis, ttl_s: int = DEDUPE_TTL_S):
        self._redis = redis
        self._ttl_s = ttl_s

    async def seen(self, update_id: int) -> bool:
        return bool(await self._redis.exists(f"aimb:upd:{update_id}"))

    async def mark(self, update_id: int) -> None:
        await self._redis.set(f"aimb:upd:{update_id}", "1", ex=self._ttl_s)
