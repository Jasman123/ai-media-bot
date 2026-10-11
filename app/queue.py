"""Redis/ARQ wiring. Two queues so chat stays responsive while generation jobs run (C3)."""

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

QUEUE_UPDATES = "aimb:updates"   # Telegram updates: fast, user-facing
QUEUE_JOBS = "aimb:jobs"         # generation, polling, delivery, cron work
ALL_QUEUES = (QUEUE_UPDATES, QUEUE_JOBS)


async def create_redis(url: str) -> ArqRedis:
    return await create_pool(RedisSettings.from_dsn(url), default_queue_name=QUEUE_JOBS)
