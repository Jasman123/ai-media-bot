import logging

from ..domain.errors import InvalidUpdate, QueueUnavailable
from ..domain.ports import TaskQueue, UpdateDeduper
from ..domain.tasks import PROCESS_UPDATE, QUEUE_UPDATES

logger = logging.getLogger(__name__)


class UpdateIntake:
    """Accepts a Telegram update at most once into the durable queue (C3), never losing it.

    Order matters: the dedupe marker is written only *after* a successful enqueue, so a failure anywhere leaves no
    marker and Telegram's redelivery is processed. Concurrent duplicate deliveries are collapsed by the queue's
    job-id idempotency (`upd:<update_id>`), which also covers the window before the marker is written.
    """

    def __init__(self, deduper: UpdateDeduper, queue: TaskQueue):
        self._deduper = deduper
        self._queue = queue

    async def accept(self, update: dict) -> bool:
        """Returns False for a duplicate. Raises QueueUnavailable so the caller can ask Telegram to retry."""
        update_id = update.get("update_id")
        if not isinstance(update_id, int) or isinstance(update_id, bool):
            raise InvalidUpdate("missing integer update_id")
        try:
            if await self._deduper.seen(update_id):
                return False
            enqueued = await self._queue.enqueue(PROCESS_UPDATE, update, queue=QUEUE_UPDATES,
                                                 job_id=f"upd:{update_id}")
        except Exception as exc:
            logger.exception("Update intake failed", extra={"update_id": update_id})
            raise QueueUnavailable() from exc
        try:
            await self._deduper.mark(update_id)
        except Exception:
            # Already queued; the job id still dedupes redeliveries while the job/result is kept.
            logger.warning("Could not record update as seen", extra={"update_id": update_id})
        return enqueued
