"""Provider webhooks (primary completion signal; polling is the fallback)."""

import logging
from collections.abc import Mapping

from ..domain.jobs import JobRepository
from ..domain.ports import TaskQueue
from ..domain.tasks import APPLY_PROVIDER_STATUS, QUEUE_JOBS
from .providers import ProviderRegistry

logger = logging.getLogger(__name__)


class ProviderWebhookIntake:
    def __init__(self, registry: ProviderRegistry, jobs: JobRepository, queue: TaskQueue):
        self._registry = registry
        self._jobs = jobs
        self._queue = queue

    async def handle(self, provider_name: str, headers: Mapping[str, str], body: bytes) -> bool:
        """Verify + normalise via the adapter, then hand the status to the worker. Raises NotFound for an unknown
        provider and InvalidInput for a bad signature. Returns False when the event is irrelevant."""
        provider = self._registry.get(provider_name)
        event = await provider.parse_webhook(headers, body)
        if event is None:
            return False
        job = await self._jobs.find_by_provider_job(provider_name, event.provider_job_id)
        if job is None:
            logger.info("Webhook for unknown job", extra={"provider": provider_name})
            return False
        await self._queue.enqueue(APPLY_PROVIDER_STATUS, job.id, event.status, queue=QUEUE_JOBS,
                                  job_id=f"evt:{job.id}:{event.status.state.value}")
        return True
