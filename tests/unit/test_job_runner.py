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
    assert done.result_refs["file_ids"] == ["fid-0"]
    assert done.result_refs["steps"] == {"file:0": {"file_ids": ["fid-0"]}}
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
    await p.runner.poll(job.id, (await p.jobs.get(job.id)).poll_seq)
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


async def test_no_model_available_refunds():
    p = await build_pipeline()
    await p.fund(paid=10)
    job = await p.generation.create(1, IMAGE)
    await p.catalog.set_provider_enabled("fake", False)
    await p.drain()
    assert (await p.jobs.get(job.id)).error_code == FailureReason.NO_MODEL.value


# ---------------------------------------------------------------------------- delivery outbox
async def test_transient_delivery_error_retries_from_storage_without_refund():
    from app.domain.jobs import DeliveryRetry

    p = await build_pipeline(channel=RecordingChannel(error=DeliveryRetry("telegram 502", retry_after_s=7)))
    await p.fund(paid=10)
    job = await p.generation.create(1, IMAGE)
    p.queue.jobs.clear()
    await p.runner.run(job.id)
    waiting = await p.jobs.get(job.id)
    assert waiting.status == JobStatus.DELIVERING and waiting.lease_owner is None
    assert waiting.result_keys and waiting.deliver_attempts == 1
    retry = p.queue.jobs[-1]
    assert retry["task"] == "deliver_job" and retry["defer_s"] == 7  # Telegram's retry_after is respected
    submissions = len(p.provider.submitted)
    p.channel.error = None
    await p.drain()
    assert (await p.jobs.get(job.id)).status == JobStatus.DELIVERED
    assert len(p.provider.submitted) == submissions  # no second generation; the stored copy is re-used
    assert (await p.balance()).paid == 8


async def test_partial_delivery_then_retry_never_resends_or_refunds():
    """Reviewer scenario #2: the album went out, then a 429 hit the next step."""
    from app.domain.jobs import DeliveryRetry

    p = await build_pipeline(channel=RecordingChannel(error=DeliveryRetry("429"), fail_after=1))
    await p.fund(paid=10)
    job = await p.generation.create(1, replace(IMAGE, params={"n": 2}))
    p.queue.jobs.clear()
    await p.runner.run(job.id)
    assert p.channel.sent_steps == ["file:0"]
    p.clock.advance(seconds=p.policy.image_timeout_s + 1)  # past the deadline: still no refund
    assert (await p.runner.sweep())["timed_out"] == 0
    p.channel.error = None
    await p.drain()
    assert p.channel.sent_steps == ["file:0", "file:1"]  # file 0 is not sent twice
    assert (await p.jobs.get(job.id)).status == JobStatus.DELIVERED
    assert (await p.balance()).paid == 6


async def test_exhausted_delivery_after_partial_send_counts_as_delivered():
    from app.domain.jobs import DeliveryRetry

    policy = JobPolicy(image_poll_s=0, video_poll_s=0, deliver_backoff_s=0, max_deliver_attempts=2)
    p = await build_pipeline(policy=policy, channel=RecordingChannel(error=DeliveryRetry("down"), fail_after=1))
    await p.fund(paid=10)
    job = await p.generation.create(1, replace(IMAGE, params={"n": 2}))
    p.queue.jobs.clear()
    await p.runner.run(job.id)  # sends file 0, then fails
    p.channel.fail_after = None  # every later attempt fails immediately
    await p.drain()
    final = await p.jobs.get(job.id)
    assert final.status == JobStatus.DELIVERED and final.error_code == "partial_delivery"
    assert p.ledger_repo.reservations[job.reservation_id].status.value == "captured"
    assert p.channel.failures == []


async def test_exhausted_delivery_with_nothing_sent_refunds_once():
    from app.domain.jobs import DeliveryRetry

    policy = JobPolicy(image_poll_s=0, video_poll_s=0, deliver_backoff_s=0, max_deliver_attempts=3)
    p = await build_pipeline(policy=policy, channel=RecordingChannel(error=DeliveryRetry("down")))
    await p.fund(paid=10)
    job = await p.generation.create(1, IMAGE)
    await p.drain()
    final = await p.jobs.get(job.id)
    assert final.status == JobStatus.REFUNDED and final.deliver_attempts == 3
    assert (await p.balance()).paid == 10
    assert [f[1] for f in p.channel.failures] == [FailureReason.DELIVERY]


async def test_stuck_delivery_is_taken_over_and_the_old_worker_cannot_capture():
    """Reviewer scenario #1: worker A stalls mid-delivery; past the lease (and the deadline) the sweeper hands the
    job to another worker. It must be re-delivered, never refunded, and captured exactly once."""
    from app.domain.jobs import LeaseLost

    p = await build_pipeline()
    await p.fund(paid=10)
    job = await p.generation.create(1, IMAGE)
    p.queue.jobs.clear()
    status = p.provider._completed(IMAGE.request("fake/instant"))
    await p.jobs.transition(job.id, (JobStatus.QUEUED,), JobStatus.DELIVERING, provider="fake",
                            provider_job_id="fake-a", result_urls=status.result.urls, result_mime="image/png")
    stalled = await p.jobs.transition(job.id, (JobStatus.DELIVERING,), JobStatus.DELIVERING,
                                      lease_owner="worker-a", lease_until=p.clock.now())
    p.clock.advance(seconds=p.policy.image_timeout_s + 600)
    counts = await p.runner.sweep()
    assert counts["timed_out"] == 0 and counts["redelivered"] == 1
    await p.drain()
    assert (await p.jobs.get(job.id)).status == JobStatus.DELIVERED
    with pytest.raises(LeaseLost):  # worker A wakes up: its fenced writes are refused
        await p.runner._fenced(stalled, "worker-a", result_keys=("x",))
    assert p.ledger_repo.reservations[job.reservation_id].status.value == "captured"
    assert (await p.balance()).paid == 8 and p.channel.failures == []


async def test_unreachable_user_after_partial_delivery_is_charged_not_refunded():
    p = await build_pipeline(channel=RecordingChannel(error=ChannelUnreachable("blocked"), fail_after=1))
    await p.fund(paid=10)
    job = await p.generation.create(1, replace(IMAGE, params={"n": 2}))
    await p.drain()
    final = await p.jobs.get(job.id)
    assert final.status == JobStatus.DELIVERED and final.error_code == "partial_delivery"
    assert (await p.balance()).paid == 6


# ---------------------------------------------------------------------------- submit / poll races
async def test_slow_submit_overtaken_by_sweeper_cancels_the_orphan():
    """Reviewer scenario #3: the sweeper requeues a job while its submit is still in flight."""
    p = await build_pipeline(delay_polls=100)
    await p.fund(paid=100)
    job = await p.generation.create(1, VIDEO)
    p.queue.jobs.clear()
    original_submit = p.provider.submit

    async def slow_submit(req):
        result = await original_submit(req)
        p.clock.advance(seconds=p.policy.submit_lease_s + p.policy.stuck_after_s + 1)
        await p.runner.sweep()
        return result

    p.provider.submit = slow_submit
    await p.runner.run(job.id)
    requeued = await p.jobs.get(job.id)
    assert requeued.status == JobStatus.QUEUED and requeued.provider_job_id is None
    orphan = next(iter(p.provider._jobs.values()))
    assert orphan.cancelled


async def test_completed_without_results_refunds():
    from app.domain.generation import ProviderResult

    p = await build_pipeline(delay_polls=100)
    await p.fund(paid=100)
    job = await p.generation.create(1, VIDEO)
    await p.runner.run(job.id)
    await p.runner.apply_status(job.id, ProviderStatus(ProviderState.COMPLETED,
                                                       result=ProviderResult(urls=(), mime="video/mp4")))
    assert (await p.jobs.get(job.id)).status == JobStatus.REFUNDED
    assert (await p.balance()).paid == 100


async def test_status_api_errors_keep_polling_until_deadline():
    from app.domain.generation import ProviderDown

    p = await build_pipeline(delay_polls=100)
    await p.fund(paid=100)
    job = await p.generation.create(1, VIDEO)
    p.queue.jobs.clear()
    await p.runner.run(job.id)

    async def broken(provider_job_id):
        raise ProviderDown("status endpoint 503")

    p.provider.status = broken
    seq = (await p.jobs.get(job.id)).poll_seq
    await p.runner.poll(job.id, seq)
    assert (await p.jobs.get(job.id)).status == JobStatus.RUNNING
    assert p.queue.jobs[-1]["task"] == POLL_JOB  # still polling
    p.clock.advance(seconds=p.policy.video_timeout_s + 1)
    await p.runner.poll(job.id, seq + 1)
    assert (await p.jobs.get(job.id)).status == JobStatus.REFUNDED


async def test_superseded_poll_chain_stops():
    p = await build_pipeline(delay_polls=100)
    await p.fund(paid=100)
    job = await p.generation.create(1, VIDEO)
    p.queue.jobs.clear()
    await p.runner.run(job.id)
    old_seq = (await p.jobs.get(job.id)).poll_seq
    assert await p.runner.revive_polling(await p.jobs.get(job.id))
    p.queue.jobs.clear()
    await p.runner.poll(job.id, old_seq)  # the old chain's next poll…
    assert p.queue.jobs == []              # …does nothing and schedules nothing


async def test_requeue_lease_respects_retry_after():
    from app.domain.generation import Retryable

    p = await build_pipeline()
    await p.fund(paid=10)
    job = await p.generation.create(1, IMAGE)

    async def limited(req):
        raise Retryable("429", retry_after_s=600)

    p.provider.submit = limited
    p.queue.jobs.clear()
    await p.runner.run(job.id)
    assert p.queue.jobs[-1]["defer_s"] == 600
    p.clock.advance(seconds=300)
    assert (await p.runner.sweep())["requeued"] == 0  # not swept before its retry is due


# ---------------------------------------------------------------------------- sweeper
async def test_sweeper_requeues_lost_jobs_and_revives_dead_poll_chains():
    p = await build_pipeline(delay_polls=100)
    await p.fund(paid=500)
    queued = await p.generation.create(1, IMAGE)
    running = await p.generation.create(1, VIDEO)
    overdue = await p.generation.create(1, VIDEO)
    p.queue.jobs.clear()
    await p.runner.run(running.id)
    await p.jobs.transition(overdue.id, (JobStatus.QUEUED,), JobStatus.QUEUED,
                            deadline_at=p.clock.now() - timedelta(seconds=1))
    p.queue.jobs.clear()  # the poll chain and the queued job's run are "lost"
    p.clock.advance(seconds=p.policy.stuck_after_s + p.policy.video_poll_s + 1)
    counts = await p.runner.sweep()
    assert counts == {"requeued": 1, "timed_out": 1, "redelivered": 0, "polled": 1, "errors": 0}
    tasks = sorted((j["task"], j["args"][0]) for j in p.queue.jobs)
    assert tasks == sorted([(RUN_JOB, queued.id), (POLL_JOB, running.id)])
    assert (await p.jobs.get(overdue.id)).status == JobStatus.REFUNDED
    p.queue.jobs.clear()  # leases were renewed: an immediate second sweep does nothing
    assert await p.runner.sweep() == {"requeued": 0, "timed_out": 0, "redelivered": 0, "polled": 0, "errors": 0}


async def test_sweeper_isolates_failures_per_job():
    p = await build_pipeline(delay_polls=100)
    await p.fund(paid=500)
    a = await p.generation.create(1, VIDEO)
    b = await p.generation.create(1, VIDEO)
    p.clock.advance(seconds=p.policy.video_timeout_s + 1)
    original = p.runner.timeout

    async def flaky(job):
        if job.id == a.id:
            raise RuntimeError("boom")
        await original(job)

    p.runner.timeout = flaky
    counts = await p.runner.sweep()
    assert counts["errors"] == 1 and counts["timed_out"] == 1
    assert (await p.jobs.get(b.id)).status == JobStatus.REFUNDED


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
