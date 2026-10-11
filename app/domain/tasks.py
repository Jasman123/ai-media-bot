"""Logical queue and task names shared by producers (services, API) and the worker."""

QUEUE_UPDATES = "aimb:updates"   # Telegram updates: fast, user-facing
QUEUE_JOBS = "aimb:jobs"         # generation, polling, delivery, cron work
ALL_QUEUES = (QUEUE_UPDATES, QUEUE_JOBS)

PROCESS_UPDATE = "process_update"

RUN_JOB = "run_job"
POLL_JOB = "poll_job"
APPLY_PROVIDER_STATUS = "apply_provider_status"
SWEEP_JOBS = "sweep_jobs"
EXPIRE_FREE_CREDITS = "expire_free_credits"
