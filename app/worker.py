"""ARQ worker entrypoint. One process per queue:

    python -m app.worker updates     # Telegram updates
    python -m app.worker jobs        # generation / delivery / cron

Task functions are thin: dependencies are injected per job (dishka REQUEST scope, opened and closed by the ARQ
job hooks), the work itself lives in services.
"""

import argparse
import asyncio
import logging
from collections.abc import Callable, Sequence

from arq import Retry
from arq.connections import ArqRedis
from arq.cron import CronJob
from arq.worker import Worker
from dishka import AsyncContainer
from dishka.integrations.arq import FromDishka, inject, setup_dishka

from .bot.dispatcher import Dispatcher
from .config import Settings
from .di import make_container, warm_up
from .domain.tasks import QUEUE_JOBS, QUEUE_UPDATES
from .infra.telegram import TelegramError
from .logging_setup import configure_logging

logger = logging.getLogger(__name__)
MAX_TELEGRAM_RETRIES = 3


@inject
async def process_update(ctx: dict, update: dict, dispatcher: FromDishka[Dispatcher]) -> None:
    try:
        await dispatcher.dispatch(update)
    except TelegramError as exc:
        if exc.retry_after and ctx.get("job_try", 1) < MAX_TELEGRAM_RETRIES:
            raise Retry(defer=exc.retry_after) from exc
        logger.warning("Telegram error while handling update: %s", exc, extra={"update_id": update.get("update_id")})


FUNCTIONS: dict[str, list[Callable]] = {
    QUEUE_UPDATES: [process_update],
    QUEUE_JOBS: [],
}
CRON_JOBS: dict[str, list[CronJob]] = {QUEUE_UPDATES: [], QUEUE_JOBS: []}


def create_worker(container: AsyncContainer, redis: ArqRedis, queue: str, *, burst: bool = False,
                  poll_delay: float = 0.5, functions: Sequence[Callable] | None = None) -> Worker:
    worker = Worker(
        functions=list(functions if functions is not None else FUNCTIONS[queue]),
        cron_jobs=CRON_JOBS[queue] or None,
        queue_name=queue,
        redis_pool=redis,
        burst=burst,
        handle_signals=not burst,
        poll_delay=poll_delay,
        max_jobs=20,
        job_timeout=600,
        keep_result=3600,
    )
    setup_dishka(container, worker)
    return worker


async def _run(queue: str) -> None:
    container = make_container()
    settings = await container.get(Settings)
    configure_logging(settings.log_level, settings.log_json)
    await warm_up(container)
    worker = create_worker(container, await container.get(ArqRedis), queue)
    try:
        await worker.async_run()
    except asyncio.CancelledError:
        pass
    finally:
        # Let cancelled jobs finish their cleanup (session close, result write) before the pools go away.
        await asyncio.gather(*worker.tasks.values(), return_exceptions=True)
        await container.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="ai-media-bot worker")
    parser.add_argument("queue", choices=["updates", "jobs"])
    args = parser.parse_args()
    asyncio.run(_run(QUEUE_UPDATES if args.queue == "updates" else QUEUE_JOBS))


if __name__ == "__main__":
    main()
