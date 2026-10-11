"""Balance screen and the expiry job through the running stack."""

from datetime import timedelta

from app.domain.billing import Bucket, EntryKind
from app.services.ledger import LedgerService
from app.services.users import UserService


async def grant(stack, telegram_id, amount, bucket, days=None):
    async with stack.scope() as request:
        user = await (await request.get(UserService)).get_by_telegram_id(telegram_id)
        ledger = await request.get(LedgerService)
        from datetime import datetime, timezone

        expires = datetime.now(timezone.utc) + timedelta(days=days) if days is not None else None
        await ledger.grant(user.id, amount, bucket, EntryKind.ADMIN_GRANT, expires_at=expires)
        return user.id


async def test_credits_screen_shows_buckets_and_free_expiry(stack):
    user = stack.user(9001)
    await user.send("/start")
    await user.press("lang:id")
    await grant(stack, 9001, 20, Bucket.FREE, days=14)
    await grant(stack, 9001, 1300, Bucket.PAID)

    await user.press("cred:show")
    text = user.last_text()
    assert "Berbayar: <b>1.300</b>" in text
    assert "Gratis: <b>20</b> (berlaku sampai" in text
    assert "hanya bisa dipakai untuk gambar" in text
    assert "Total: <b>1.320</b>" in text

    await user.send("/saldo")
    assert "Total: <b>1.320</b>" in user.last_text()


async def test_expiry_task_runs_on_the_jobs_queue(stack):
    user = stack.user(9002)
    await user.send("/start")
    user_id = await grant(stack, 9002, 20, Bucket.FREE, days=-1)   # already expired
    await grant(stack, 9002, 5, Bucket.PAID)

    await stack.redis.enqueue_job("expire_free_credits", _queue_name="aimb:jobs")
    await stack.drain()

    async with stack.scope() as request:
        summary = await (await request.get(LedgerService)).summary(user_id)
    assert (summary.balance.free, summary.balance.paid) == (0, 5)


async def test_english_credits_screen(stack):
    user = stack.user(9003, lang="en")
    await user.send("/start")
    await user.press("lang:en")
    await grant(stack, 9003, 1300, Bucket.PAID)
    await user.send("/balance")
    assert "Paid: <b>1,300</b>" in user.last_text()
