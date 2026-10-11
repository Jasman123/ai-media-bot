"""ARQ adapter for the TaskQueue port."""

from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from ..domain.tasks import QUEUE_JOBS


async def create_redis(url: str) -> ArqRedis:
    return await create_pool(RedisSettings.from_dsn(url), default_queue_name=QUEUE_JOBS)


class ArqTaskQueue:
    def __init__(self, redis: ArqRedis):
        self._redis = redis

    async def enqueue(self, task: str, *args: Any, queue: str, job_id: str | None = None,
                      defer_s: float | None = None) -> bool:
        job = await self._redis.enqueue_job(task, *args, _queue_name=queue, _job_id=job_id, _defer_by=defer_s)
        return job is not None
