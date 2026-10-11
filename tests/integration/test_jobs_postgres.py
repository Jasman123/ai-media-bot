"""Job repository and pipeline endpoints against Postgres/Redis."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.domain.generation import Operation
from app.domain.jobs import Job, JobStatus
from app.domain.users import TelegramIdentity
from app.infra.clock import SystemClock
from app.infra.repositories.jobs import SqlJobRepository
from app.infra.repositories.users import SqlUserRepository


async def make_job(sessionmaker, **overrides) -> Job:
    async with sessionmaker() as session:
        user, _ = await SqlUserRepository(session, SystemClock()).upsert_seen(TelegramIdentity(6001))
        now = datetime.now(timezone.utc)
        job = Job(id=str(uuid.uuid4()), user_id=user.id, operation=Operation.TEXT2VID, tier="hemat",
                  model_id="vid-hemat-fake", status=JobStatus.QUEUED, credits_charged=35,
                  params={"duration_s": 5}, input_keys=("inputs/a.jpg",), chat_ref={"chat_id": 6001},
                  cost_usd_estimate=Decimal("0.15"), created_at=now, deadline_at=now + timedelta(minutes=15))
        job = Job(**{**job.__dict__, **overrides})
        await SqlJobRepository(session).add(job)
        await session.commit()
        return job


async def test_roundtrip_preserves_every_field(sessionmaker):
    job = await make_job(sessionmaker)
    async with sessionmaker() as session:
        loaded = await SqlJobRepository(session).get(job.id)
    assert loaded.params == {"duration_s": 5} and loaded.input_keys == ("inputs/a.jpg",)
    assert loaded.chat_ref == {"chat_id": 6001} and loaded.cost_usd_estimate == Decimal("0.15")
    assert loaded.heartbeat_at is not None


async def test_only_one_concurrent_claim_wins(sessionmaker):
    job = await make_job(sessionmaker)

    async def claim():
        async with sessionmaker() as session:
            result = await SqlJobRepository(session).transition(job.id, (JobStatus.QUEUED,), JobStatus.RUNNING,
                                                                attempts_increment=1)
            await session.commit()
            return result is not None

    results = await asyncio.gather(*[claim() for _ in range(10)])
    assert results.count(True) == 1
    async with sessionmaker() as session:
        assert (await SqlJobRepository(session).get(job.id)).attempts == 1


async def test_stale_overdue_provider_lookup_and_queue_position(sessionmaker):
    now = datetime.now(timezone.utc)
    old = await make_job(sessionmaker, created_at=now - timedelta(minutes=10))
    late = await make_job(sessionmaker, deadline_at=now - timedelta(seconds=1))
    fresh = await make_job(sessionmaker)
    async with sessionmaker() as session:
        repo = SqlJobRepository(session)
        await repo.update(late.id, provider="fake", provider_job_id="fake-123", result_keys=("r/1.png",))
        await session.commit()
        stale = {j.id for j in await repo.stale(JobStatus.QUEUED, now - timedelta(minutes=5))}
        assert stale == {old.id}
        assert {j.id for j in await repo.overdue(now)} == {late.id}
        found = await repo.find_by_provider_job("fake", "fake-123")
        assert found.id == late.id and found.result_keys == ("r/1.png",)
        assert await repo.queue_position(old) == 1
        assert await repo.queue_position(fresh) == 3
        assert await repo.count_active(old.user_id) == 3


async def test_media_endpoint_serves_only_valid_signatures(stack):
    from app.domain.ports import Storage

    storage = await stack.di.get(Storage)
    await storage.put("inputs/test/a.png", b"\x89PNG", "image/png")
    url = await storage.url("inputs/test/a.png", 60)
    path = url.replace("http://test", "")
    ok = await stack.http.get(path)
    assert ok.status_code == 200 and ok.content == b"\x89PNG" and ok.headers["content-type"] == "image/png"
    assert (await stack.http.get(path.replace("sig=", "sig=0"))).status_code == 404
    assert (await stack.http.get("/media/inputs/test/a.png?exp=1&sig=x")).status_code == 404
    assert (await stack.http.get("/media/../../etc/passwd?exp=9999999999&sig=x")).status_code == 404


async def test_provider_webhook_endpoint_rejects_bad_requests(stack):
    assert (await stack.http.post("/webhook/provider/nope", content=b"{}")).status_code == 404
    bad = await stack.http.post("/webhook/provider/fake", content=b'{"job_id": "x", "status": "OK"}',
                                headers={"x-fake-signature": "bad"})
    assert bad.status_code == 401
