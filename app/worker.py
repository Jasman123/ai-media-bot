"""ARQ workers. Run one process per queue:

    python -m app.worker updates     # Telegram updates
    python -m app.worker jobs        # generation / delivery / cron
"""

import argparse
import asyncio
import logging
from collections.abc import Callable, Sequence

from arq import Retry
from arq.cron import CronJob
from arq.worker import Worker

from .bot.router import dispatch
from .config import get_settings
from .container import Container, build_container
from .logging_setup import configure_logging
from .queue import QUEUE_JOBS, QUEUE_UPDATES
from .telegram import TelegramError

logger = logging.getLogger(__name__)


async def process_update(ctx: dict, update: dict) -> None:
    container: Container = ctx["container"]
    try:
        await dispatch(container, container.router, update)
    except TelegramError as exc:
        if exc.retry_after and ctx.get("job_try", 1) < 3:
            raise Retry(defer=exc.retry_after) from exc
        logger.warning("Telegram error while handling update: %s", exc, extra={"update_id": update.get("update_id")})


FUNCTIONS: dict[str, list[Callable]] = {
    QUEUE_UPDATES: [process_update],
    QUEUE_JOBS: [],
}
CRON_JOBS: dict[str, list[CronJob]] = {QUEUE_UPDATES: [], QUEUE_JOBS: []}


def create_worker(container: Container, queue: str, *, burst: bool = False, poll_delay: float = 0.5,
                  functions: Sequence[Callable] | None = None, **kwargs) -> Worker:
    return Worker(
        functions=list(functions if functions is not None else FUNCTIONS[queue]),
        cron_jobs=CRON_JOBS[queue] or None,
        queue_name=queue,
        redis_pool=container.redis,
        ctx={"container": container},
        burst=burst,
        handle_signals=not burst,
        poll_delay=poll_delay,
        max_jobs=20,
        job_timeout=600,
        keep_result=3600,
        **kwargs,
    )


async def _run(queue: str) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    container = await build_container(settings)
    worker = create_worker(container, queue)
    try:
        await worker.async_run()
    except asyncio.CancelledError:
        pass
    finally:
        await container.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="ai-media-bot worker")
    parser.add_argument("queue", choices=["updates", "jobs"])
    args = parser.parse_args()
    asyncio.run(_run(QUEUE_UPDATES if args.queue == "updates" else QUEUE_JOBS))


if __name__ == "__main__":
    main()
