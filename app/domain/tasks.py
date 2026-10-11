"""Logical queue and task names shared by producers (services, API) and the worker."""

QUEUE_UPDATES = "aimb:updates"   # Telegram updates: fast, user-facing
QUEUE_JOBS = "aimb:jobs"         # generation, polling, delivery, cron work
ALL_QUEUES = (QUEUE_UPDATES, QUEUE_JOBS)

PROCESS_UPDATE = "process_update"
