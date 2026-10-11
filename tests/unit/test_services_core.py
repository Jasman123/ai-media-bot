"""Application services with in-memory fakes injected through their constructors."""

import pytest

from app.domain.errors import InvalidUpdate, NotFound, QueueUnavailable
from app.domain.tasks import PROCESS_UPDATE, QUEUE_UPDATES
from app.domain.users import TelegramIdentity, normalize_language
from app.services.updates import UpdateIntake
from app.services.users import UserService
from tests.support.fakes import FakeUnitOfWork, InMemoryDeduper, InMemoryUserRepository, RecordingTaskQueue


@pytest.fixture
def users():
    return UserService(InMemoryUserRepository(), FakeUnitOfWork())


async def test_register_seen_creates_once_and_commits(users):
    first, created = await users.register_seen(TelegramIdentity(10, "sari", "en"))
    again, created_again = await users.register_seen(TelegramIdentity(10, "sari2", "en"))
    assert created and not created_again
    assert first.id == again.id and again.username == "sari2"
    assert first.language == "id"  # D1 default even for an English client
    assert users._uow.commits == 2


async def test_choose_language_normalizes_unknown_codes(users):
    user, _ = await users.register_seen(TelegramIdentity(11))
    assert (await users.choose_language(user.id, "en")).language == "en"
    updated = await users.choose_language(user.id, "fr")
    assert updated.language == "id" and updated.language_chosen


async def test_ban_and_unban(users):
    await users.register_seen(TelegramIdentity(12))
    banned = await users.set_banned(12, True, "spam")
    assert banned.is_banned and banned.ban_reason == "spam"
    unbanned = await users.set_banned(12, False, "ignored")
    assert not unbanned.is_banned and unbanned.ban_reason is None
    with pytest.raises(NotFound):
        await users.set_banned(999, True)


@pytest.mark.parametrize("code,expected", [("id", "id"), ("en", "en"), (None, "id"), ("en-US", "id")])
def test_normalize_language(code, expected):
    assert normalize_language(code) == expected


async def test_intake_enqueues_once_with_job_id():
    queue, dedupe = RecordingTaskQueue(), InMemoryDeduper()
    intake = UpdateIntake(dedupe, queue)
    assert await intake.accept({"update_id": 5, "message": {}}) is True
    assert await intake.accept({"update_id": 5, "message": {}}) is False
    assert queue.jobs == [{"task": PROCESS_UPDATE, "args": ({"update_id": 5, "message": {}},),
                           "queue": QUEUE_UPDATES, "job_id": "upd:5", "defer_s": None}]


@pytest.mark.parametrize("update", [{}, {"update_id": "5"}, {"update_id": True}, {"update_id": None}])
async def test_intake_rejects_malformed(update):
    with pytest.raises(InvalidUpdate):
        await UpdateIntake(InMemoryDeduper(), RecordingTaskQueue()).accept(update)


async def test_intake_leaves_no_marker_when_queue_is_down():
    dedupe, queue = InMemoryDeduper(), RecordingTaskQueue(fail=True)
    intake = UpdateIntake(dedupe, queue)
    with pytest.raises(QueueUnavailable):
        await intake.accept({"update_id": 9})
    assert 9 not in dedupe.ids
    queue.fail = False
    assert await intake.accept({"update_id": 9}) is True  # Telegram's redelivery is processed


async def test_intake_survives_marker_failure_after_enqueue():
    """The update is already queued; a failing marker must not turn into an error or a lost update."""
    queue = RecordingTaskQueue()
    intake = UpdateIntake(InMemoryDeduper(fail_mark=True), queue)
    assert await intake.accept({"update_id": 11}) is True
    assert await intake.accept({"update_id": 11}) is False  # still deduped by the queue's job id
    assert len(queue.jobs) == 1


async def test_intake_maps_dedupe_store_failure_to_queue_unavailable():
    class Down:
        async def seen(self, update_id):
            raise ConnectionError("redis down")

    with pytest.raises(QueueUnavailable):
        await UpdateIntake(Down(), RecordingTaskQueue()).accept({"update_id": 12})
