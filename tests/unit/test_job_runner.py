"""GenerationService + JobRunner with in-memory fakes and the live FakeProvider: every branch of the state machine."""

from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.billing import InsufficientCredits
from app.domain.generation import (
    InvalidInput,
    Operation,
    ProviderState,
    ProviderStatus,
)
from app.domain.jobs import ChannelUnreachable, FailureReason, JobPolicy, JobStatus
from app.domain.tasks import APPLY_PROVIDER_STATUS, POLL_JOB, RUN_JOB
from app.services.generation import JobDraft
from app.services.provider_webhooks import ProviderWebhookIntake
from app.services.providers import ProviderRegistry
from tests.support.fakes import RecordingChannel, RecordingTaskQueue
from tests.support.pipeline import build_pipeline

IMAGE = JobDraft(Operation.TEXT2IMG, "fast", prompt="kucing batik", params={"aspect_ratio": "1:1", "n": 1},
                 chat_ref={"chat_id": 7})
VIDEO = JobDraft(Operation.TEXT2VID, "hemat", prompt="ombak", params={"duration_s": 5, "aspect_ratio": "9:16"},
                 chat_ref={"chat_id": 7})


async def test_image_job_end_to_end_with_fake_provider():
    p = await build_pipeline()
    await p.fund(paid=10)
    job = await p.generation.create(1, IMAGE)
    assert job.status == JobStatus.QUEUED and job.credits_charged == 2 and not job.watermark
    assert p.queue.jobs[0]["task"] == RUN_JOB and p.queue.jobs[0]["job_id"] == f"run:{job.id}:0"
    assert (await p.balance()).paid == 8  # reserved immediately

    await p.drain()
    done = await p.jobs.get(job.id)
    assert done.status == JobStatus.DELIVERED and done.provider == "fake" and done.result_keys
    assert done.result_refs == {"file_ids": ["fid-0"]}
    assert done.result_keys[0] in p.storage.objects
    assert len(p.channel.delivered) == 1 and p.channel.delivered[0][1][0].mime == "image/png"
    reservation = p.ledger_repo.reservations[job.reservation_id]
    assert reservation.status.value == "captured"
    assert (await p.balance()).paid == 8


async def test_free_credits_mark_watermark_and_video_cannot_use_them():
    p = await build_pipeline()
    await p.fund(free=20)
    job = await p.generation.create(1, IMAGE)
    assert job.watermark and job.free_credits_used == 2
    with pytest.raises(InsufficientCredits):
        await p.generation.create(1, VIDEO)
    assert len(p.jobs.jobs) == 1


async def test_delayed_video_polls_until_complete():
    p = await build_pipeline(delay_polls=3)
    await p.fund(paid=100)
    job = await p.generation.create(1, VIDEO)
    assert job.credits_charged == 35
    await p.drain()
    done = await p.jobs.get(job.id)
    assert done.status == JobStatus.DELIVERED
    assert done.attempts == 1
    assert len(p.channel.progress_updates) == 2  # two "still running" polls before completion
    assert p.channel.delivered[0][1][0].mime == "video/mp4"


@pytest.mark.parametrize("marker,reason", [
    ("[[fail]]", FailureReason.PROVIDER_ERROR),
    ("[[moderation]]", FailureReason.MODERATION),
    ("[[invalid]]", FailureReason.INVALID_INPUT),
])
async def test_failures_refund_and_notify(marker, reason):
    p = await build_pipeline()
    await p.fund(paid=10)
    job = await p.generation.create(1, replace(IMAGE, prompt=f"x {marker}"))
    await p.drain()
    failed = await p.jobs.get(job.id)
    assert failed.status == JobStatus.REFUNDED and failed.error_code == reason.value
    assert (await p.balance()).paid == 10
    assert p.channel.failures == [(failed, reason, True)]


async def test_provider_down_uses_fallback_model():
    p = await build_pipeline()
    await p.catalog.update("vid-pro-fake", {"provider_model_id": "fake/down"})
    await p.fund(paid=200)
    job = await p.generation.create(1, replace(VIDEO, tier="pro"))
    assert job.model_id == "vid-pro-fake" and job.credits_charged == 100
    await p.drain()
    done = await p.jobs.get(job.id)
    assert done.status == JobStatus.DELIVERED and done.model_id == "vid-hemat-fake"
    assert done.credits_charged == 100  # price stays attached to the tier


async def test_provider_down_without_fallback_refunds():
    p = await build_pipeline()
    await p.catalog.update("img-fast-fake", {"provider_model_id": "fake/down"})
    await p.fund(paid=10)
    job = await p.generation.create(1, IMAGE)
    await p.drain()
    assert (await p.jobs.get(job.id)).error_code == FailureReason.PROVIDER_DOWN.value
    assert (await p.balance()).paid == 10


async def test_quota_exhausted_pauses_model_and_falls_back():
    p = await build_pipeline()
    await p.catalog.update("img-quality-fake", {"provider_model_id": "fake/quota"})
    await p.fund(paid=50)
    job = await p.generation.create(1, replace(IMAGE, tier="quality"))
    await p.drain()
    assert (await p.jobs.get(job.id)).model_id == "img-fast-fake"
    assert not p.catalog_repo.models["img-quality-fake"].enabled


async def test_fallback_below_margin_floor_is_skipped():
    p = await build_pipeline()
    await p.catalog.update("vid-pro-fake", {"provider_model_id": "fake/down", "credits": 1})  # pinned tiny price
    await p.fund(paid=10)
    job = await p.generation.create(1, replace(VIDEO, tier="pro"))
    await p.drain()
    failed = await p.jobs.get(job.id)
    assert failed.status == JobStatus.REFUNDED and failed.error_code == FailureReason.PROVIDER_DOWN.value


async def test_retryable_requeues_then_succeeds_and_exhaustion_fails():
    p = await build_pipeline()
    await p.fund(paid=10)
    job = await p.generation.create(1, replace(IMAGE, prompt="x [[retry]]"))
    await p.drain()
    done = await p.jobs.get(job.id)
    assert done.status == JobStatus.DELIVERED and done.attempts == 2

    strict = await build_pipeline(policy=JobPolicy(max_submit_retries=0, image_poll_s=0, video_poll_s=0))
    await strict.fund(paid=10)
    job = await strict.generation.create(1, replace(IMAGE, prompt="y [[retry]]"))
    await strict.drain()
    assert (await strict.jobs.get(job.id)).error_code == FailureReason.PROVIDER_BUSY.value


async def test_poll_after_deadline_cancels_and_refunds():
    p = await build_pipeline(delay_polls=100)
    await p.fund(paid=100)
    job = await p.generation.create(1, VIDEO)
    await p.runner.run(job.id)
    p.clock.advance(seconds=p.policy.video_timeout_s + 1)
    await p.runner.poll(job.id)
    timed_out = await p.jobs.get(job.id)
    assert timed_out.status == JobStatus.REFUNDED and timed_out.error_code == "timeout"
    assert (await p.provider.status(timed_out.provider_job_id)).state == ProviderState.FAILED  # cancelled
    assert (await p.balance()).paid == 100


async def test_apply_status_is_idempotent():
    p = await build_pipeline(delay_polls=100)
    await p.fund(paid=100)
    job = await p.generation.create(1, VIDEO)
    await p.runner.run(job.id)
    running = await p.jobs.get(job.id)
    status = p.provider._completed(p.provider._jobs[running.provider_job_id].req)
    await p.runner.apply_status(job.id, status)
    await p.runner.apply_status(job.id, status)
    await p.runner.apply_status(job.id, ProviderStatus(ProviderState.FAILED, error=InvalidInput("late")))
    assert len(p.channel.delivered) == 1 and p.channel.failures == []
    assert (await p.jobs.get(job.id)).status == JobStatus.DELIVERED


async def test_unreachable_user_is_refunded_silently():
    p = await build_pipeline(channel=RecordingChannel(error=ChannelUnreachable("blocked")))
    await p.fund(paid=10)
    job = await p.generation.create(1, IMAGE)
    await p.drain()
    refunded = await p.jobs.get(job.id)
    assert refunded.status == JobStatus.REFUNDED and refunded.error_code == "delivery"
    assert p.channel.failures == []
    assert (await p.balance()).paid == 10


async def test_transient_delivery_error_rolls_back_to_running_and_recover_polls():
    p = await build_pipeline(channel=RecordingChannel(error=RuntimeError("telegram 502")))
    await p.fund(paid=10)
    job = await p.generation.create(1, IMAGE)
    p.queue.jobs.clear()
    with pytest.raises(RuntimeError):
        await p.runner.run(job.id)
    assert (await p.jobs.get(job.id)).status == JobStatus.RUNNING
    await p.runner.recover(job.id)
    assert p.queue.jobs[-1]["task"] == POLL_JOB
    p.channel.error = None
    await p.drain()
    assert (await p.jobs.get(job.id)).status == JobStatus.DELIVERED


async def test_no_model_available_refunds():
    p = await build_pipeline()
    await p.fund(paid=10)
    job = await p.generation.create(1, IMAGE)
    await p.catalog.set_provider_enabled("fake", False)
    await p.drain()
    assert (await p.jobs.get(job.id)).error_code == FailureReason.NO_MODEL.value


async def test_sweeper_requeues_recovers_and_times_out():
    p = await build_pipeline(delay_polls=100)
    await p.fund(paid=500)
    queued = await p.generation.create(1, IMAGE)
    running = await p.generation.create(1, VIDEO)
    overdue = await p.generation.create(1, VIDEO)
    p.queue.jobs.clear()
    await p.runner.run(running.id)
    await p.jobs.transition(overdue.id, (JobStatus.QUEUED,), JobStatus.QUEUED,
                            deadline_at=p.clock.now() - timedelta(seconds=1))
    p.queue.jobs.clear()
    p.clock.advance(seconds=p.policy.stuck_after_s + 1)
    counts = await p.runner.sweep()
    assert counts == {"requeued": 1, "timed_out": 1, "recovered": 0, "polled": 1}
    tasks = sorted((j["task"], j["args"][0]) for j in p.queue.jobs)
    assert tasks == sorted([(RUN_JOB, queued.id), (POLL_JOB, running.id)])
    assert (await p.jobs.get(overdue.id)).status == JobStatus.REFUNDED
    # a second sweep right away does nothing: heartbeats were refreshed
    p.queue.jobs.clear()
    assert await p.runner.sweep() == {"requeued": 0, "timed_out": 0, "recovered": 0, "polled": 0}


async def test_sweeper_recovers_interrupted_delivery():
    p = await build_pipeline()
    await p.fund(paid=10)
    job = await p.generation.create(1, IMAGE)
    p.queue.jobs.clear()
    await p.jobs.transition(job.id, (JobStatus.QUEUED,), JobStatus.DELIVERING, provider="fake",
                            provider_job_id="fake-x")
    p.clock.advance(seconds=p.policy.stuck_after_s + 1)
    assert (await p.runner.sweep())["recovered"] == 1
    assert (await p.jobs.get(job.id)).status == JobStatus.RUNNING


async def test_webhook_intake_enqueues_status_for_known_jobs():
    p = await build_pipeline(delay_polls=100)
    await p.fund(paid=100)
    job = await p.generation.create(1, VIDEO)
    await p.runner.run(job.id)
    running = await p.jobs.get(job.id)
    queue = RecordingTaskQueue()
    intake = ProviderWebhookIntake(ProviderRegistry([p.provider]), p.jobs, queue)
    headers, body = p.provider.signed_webhook(running.provider_job_id)
    assert await intake.handle("fake", headers, body) is True
    assert queue.jobs[0]["task"] == APPLY_PROVIDER_STATUS and queue.jobs[0]["args"][0] == job.id
    headers, body = p.provider.signed_webhook("fake-unknown")
    assert await intake.handle("fake", headers, body) is False
    with pytest.raises(InvalidInput):
        await intake.handle("fake", {"x-fake-signature": "bad"}, body)


async def test_quote_matches_create_and_queue_position():
    p = await build_pipeline()
    await p.fund(paid=100)
    quote = await p.generation.quote(replace(IMAGE, params={"n": 3}))
    assert quote.credits == 6
    first = await p.generation.create(1, IMAGE)
    p.clock.advance(seconds=1)
    second = await p.generation.create(1, IMAGE)
    assert await p.generation.queue_position(first) == 1
    assert await p.generation.queue_position(second) == 2
    updated = await p.generation.set_chat_ref(first.id, progress_message_id=55)
    assert updated.chat_ref == {"chat_id": 7, "progress_message_id": 55}
