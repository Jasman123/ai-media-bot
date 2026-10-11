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
from arq.cron import CronJob, cron
from arq.worker import Worker
from dishka import AsyncContainer
from dishka.integrations.arq import FromDishka, inject, setup_dishka

from .bot.dispatcher import Dispatcher
from .config import Settings
from .di import make_container, warm_up
from .domain.generation import ProviderStatus
from .domain.tasks import QUEUE_JOBS, QUEUE_UPDATES
from .infra.telegram import TelegramError
from .logging_setup import configure_logging
from .services.job_runner import JobRunner
from .services.ledger import LedgerService

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


@inject
async def expire_free_credits(ctx: dict, ledger: FromDishka[LedgerService]) -> int:
    """Daily: free credits past their expiry leave the balance (FIFO across grants)."""
    expired = await ledger.expire_all()
    logger.info("Expired %s free credits", expired)
    return expired


@inject
async def run_job(ctx: dict, job_id: str, runner: FromDishka[JobRunner]) -> None:
    try:
        await runner.run(job_id)
    except Exception:
        await runner.recover(job_id)
        raise


@inject
async def poll_job(ctx: dict, job_id: str, seq: int, runner: FromDishka[JobRunner]) -> None:
    try:
        await runner.poll(job_id, seq)
    except Exception:
        await runner.recover(job_id)
        raise


@inject
async def deliver_job(ctx: dict, job_id: str, runner: FromDishka[JobRunner]) -> None:
    await runner.deliver(job_id)  # failures are retried by the delivery lease logic itself


@inject
async def apply_provider_status(ctx: dict, job_id: str, status: ProviderStatus, runner: FromDishka[JobRunner]) -> None:
    try:
        await runner.apply_status(job_id, status)
    except Exception:
        await runner.recover(job_id)
        raise


@inject
async def sweep_jobs(ctx: dict, runner: FromDishka[JobRunner]) -> dict:
    """Every minute: requeue lost jobs, time out overdue ones, recover interrupted deliveries."""
    counts = await runner.sweep()
    if any(counts.values()):
        logger.info("Sweeper: %s", counts)
    return counts


FUNCTIONS: dict[str, list[Callable]] = {
    QUEUE_UPDATES: [process_update],
    QUEUE_JOBS: [run_job, poll_job, deliver_job, apply_provider_status, sweep_jobs, expire_free_credits],
}
CRON_JOBS: dict[str, list[CronJob]] = {
    QUEUE_UPDATES: [],
    QUEUE_JOBS: [
        cron(sweep_jobs, second=0, run_at_startup=True),
        # 17:05 UTC = 00:05 WIB, just after midnight in Indonesia.
        cron(expire_free_credits, hour=17, minute=5, run_at_startup=False),
    ],
}


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
