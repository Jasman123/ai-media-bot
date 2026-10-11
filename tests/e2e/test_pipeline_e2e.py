"""Phase 0 exit test (plan §7): prompt → queued job → FakeProvider → delivered result, through the real webhook,
ARQ queues, worker, Postgres ledger and fake Telegram, with zero provider spend."""

import pytest
from sqlalchemy import select

from app.domain.billing import Bucket, EntryKind
from app.domain.generation import Operation
from app.domain.jobs import JobStatus
from app.infra.models import LedgerEntryRow
from app.services.generation import GenerationService, JobDraft
from app.services.ledger import LedgerService
from app.services.users import UserService
from tests.conftest import make_settings


async def setup_user(stack, telegram_id: int, paid: int = 0, free: int = 0):
    user = stack.user(telegram_id)
    await user.send("/start")
    async with stack.scope() as request:
        account = await (await request.get(UserService)).get_by_telegram_id(telegram_id)
        ledger = await request.get(LedgerService)
        if paid:
            await ledger.grant(account.id, paid, Bucket.PAID, EntryKind.PURCHASE)
        if free:
            await ledger.grant(account.id, free, Bucket.FREE, EntryKind.SIGNUP_GIFT)
    return user, account


async def create_job(stack, account, draft: JobDraft):
    async with stack.scope() as request:
        return await (await request.get(GenerationService)).create(account.id, draft)


async def job_state(stack, job_id):
    async with stack.scope() as request:
        return await (await request.get(GenerationService)).get(job_id)


async def ledger_kinds(stack, user_id):
    async with stack.sessionmaker() as session:
        rows = await session.scalars(select(LedgerEntryRow).where(LedgerEntryRow.user_id == user_id)
                                     .order_by(LedgerEntryRow.id))
        return [(r.kind, r.bucket, r.amount) for r in rows]


async def test_phase0_exit_prompt_to_delivered_image(stack):
    user, account = await setup_user(stack, 7101, paid=10)
    job = await create_job(stack, account, JobDraft(Operation.TEXT2IMG, "fast", prompt="kucing oranye memakai batik",
                                                    params={"aspect_ratio": "1:1"},
                                                    chat_ref={"chat_id": 7101, "lang": "id"}))
    await stack.drain()

    photo = user.calls("sendPhoto")[-1]
    assert photo.files["photo"][2] == "image/png"
    assert photo.params["caption"] == "✅ Selesai! Terpakai 2 kredit."
    done = await job_state(stack, job.id)
    assert done.status == JobStatus.DELIVERED and done.provider == "fake"
    assert done.result_refs["file_ids"][0].startswith("photo-sent-")
    assert await ledger_kinds(stack, account.id) == [("purchase", "paid", 10), ("reserve", "paid", -2),
                                                      ("capture", "paid", 0)]


async def test_failed_job_auto_refunds_through_the_stack(stack):
    user, account = await setup_user(stack, 7102, paid=30)
    job = await create_job(stack, account, JobDraft(Operation.TEXT2IMG, "quality", prompt="x [[fail]]",
                                                    chat_ref={"chat_id": 7102, "lang": "id"}))
    await stack.drain()
    assert (await job_state(stack, job.id)).status == JobStatus.REFUNDED
    assert "19 kredit sudah dikembalikan" in user.last_text()
    kinds = await ledger_kinds(stack, account.id)
    assert kinds[-1] == ("release", "paid", 19)
    async with stack.scope() as request:
        assert (await (await request.get(LedgerService)).summary(account.id)).balance.paid == 30


async def test_delayed_video_with_progress_polling(stack):
    user, account = await setup_user(stack, 7103, paid=100)
    progress = await user.stack.tg.client().send_message(7103, "⏳")
    job = await create_job(stack, account, JobDraft(
        Operation.TEXT2VID, "hemat", prompt="ombak di Bali", params={"duration_s": 5, "aspect_ratio": "9:16"},
        chat_ref={"chat_id": 7103, "lang": "id", "progress_message_id": progress["message_id"]}))
    await stack.drain()
    assert user.calls("sendVideo")[-1].files["video"][2] == "video/mp4"
    assert any("Sedang diproses" in c.text for c in user.calls("editMessageText"))
    assert user.calls("deleteMessage")[-1].params["message_id"] == progress["message_id"]
    assert (await job_state(stack, job.id)).credits_charged == 35


async def test_free_credits_pay_for_images_only(stack):
    user, account = await setup_user(stack, 7104, free=20)
    job = await create_job(stack, account, JobDraft(Operation.TEXT2IMG, "fast", prompt="avatar",
                                                    chat_ref={"chat_id": 7104}))
    await stack.drain()
    done = await job_state(stack, job.id)
    assert done.status == JobStatus.DELIVERED and done.watermark and done.free_credits_used == 2
    from app.domain.billing import InsufficientCredits

    with pytest.raises(InsufficientCredits):
        await create_job(stack, account, JobDraft(Operation.TEXT2VID, "hemat", params={"duration_s": 5},
                                                  chat_ref={"chat_id": 7104}))


class TestProviderWebhookPath:
    """Provider webhook as the primary completion signal: polls never finish on their own here."""

    @pytest.fixture
    def settings(self):
        return make_settings(fake_provider_delay_polls=10_000)

    async def test_signed_webhook_completes_the_job(self, stack):
        from app.infra.providers.fake import FakeProvider
        from app.services.job_runner import JobRunner
        from app.services.providers import ProviderRegistry

        user, account = await setup_user(stack, 7105, paid=100)
        job = await create_job(stack, account, JobDraft(Operation.TEXT2VID, "hemat", prompt="hujan",
                                                        params={"duration_s": 5}, chat_ref={"chat_id": 7105}))
        await stack.redis.delete("aimb:jobs")  # run the job by hand so no poll chain runs to completion first
        async with stack.scope() as request:
            await (await request.get(JobRunner)).run(job.id)
        running = await job_state(stack, job.id)
        assert running.status == JobStatus.RUNNING

        fake: FakeProvider = (await stack.di.get(ProviderRegistry)).get("fake")
        headers, body = fake.signed_webhook(running.provider_job_id)
        resp = await stack.http.post("/webhook/provider/fake", content=body, headers=headers)
        assert resp.json() == {"ok": True, "handled": True}
        await stack.drain()
        assert (await job_state(stack, job.id)).status == JobStatus.DELIVERED
        assert len(user.calls("sendVideo")) == 1


class TestTimeout:
    @pytest.fixture
    def settings(self):
        return make_settings(fake_provider_delay_polls=10_000, job_video_timeout_s=0)

    async def test_overdue_video_is_cancelled_and_refunded(self, stack):
        user, account = await setup_user(stack, 7106, paid=100)
        job = await create_job(stack, account, JobDraft(Operation.TEXT2VID, "hemat", prompt="x",
                                                        params={"duration_s": 5},
                                                        chat_ref={"chat_id": 7106, "lang": "en"}))
        await stack.drain()
        done = await job_state(stack, job.id)
        assert done.status == JobStatus.REFUNDED and done.error_code == "timeout"
        assert "35 credits have been returned" in user.last_text()
