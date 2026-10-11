"""tests/fixtures/jobs/failure_matrix.json: provider outcome → job status, reason, refund, notice."""

import json
from pathlib import Path

import pytest

from app.domain.generation import Operation
from app.services.generation import JobDraft
from tests.support.pipeline import build_pipeline

CASES = json.loads((Path(__file__).parent.parent / "fixtures" / "jobs" / "failure_matrix.json").read_text())["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
async def test_failure_matrix(case):
    p = await build_pipeline()
    await p.catalog.update("img-fast-fake", {"provider_model_id": f"fake/{case['mode']}"})
    await p.fund(paid=10)
    job = await p.generation.create(1, JobDraft(Operation.TEXT2IMG, "fast", prompt="x", chat_ref={"chat_id": 1}))
    await p.drain()
    final = await p.jobs.get(job.id)
    assert final.status.value == case["status"]
    assert final.error_code == case["reason"]
    assert (await p.balance()).paid == (10 if case["refunded"] else 8)
    assert bool(p.channel.failures) == case["notified"]
    assert bool(p.channel.delivered) == (case["status"] == "delivered")
    if case.get("paused"):
        assert not p.catalog_repo.models["img-fast-fake"].enabled
