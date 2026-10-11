"""TelegramDelivery against the fake Bot API, and the storage adapters (local disk, S3 via botocore Stubber)."""

import io
import time

import pytest

from app.bot.delivery import PHOTO_LIMIT, UPLOAD_LIMIT, TelegramDelivery
from app.domain.generation import Operation
from app.domain.jobs import ChannelUnreachable, FailureReason, Job, JobStatus, MediaFile, Progress
from app.i18n import I18n
from app.infra.storage.local import LocalStorage, sign, verify
from app.infra.storage.s3 import S3Storage
from tests.support.fake_telegram import FakeTelegram
from tests.support.fakes import InMemoryStorage

JOB = Job(id="11111111-2222", user_id=1, operation=Operation.TEXT2IMG, tier="fast", model_id="m",
          status=JobStatus.DELIVERING, credits_charged=19, chat_ref={"chat_id": 42, "lang": "id"})


def png(size: int = 10) -> MediaFile:
    return MediaFile(b"\x89PNG" + b"0" * size, "image/png", "a.png", "results/x/0.png")


@pytest.fixture
def delivery():
    fake = FakeTelegram()
    return TelegramDelivery(fake.client(), I18n.load(), InMemoryStorage()), fake


async def test_single_photo_with_caption(delivery):
    channel, fake = delivery
    refs = await channel.deliver(JOB, [png()])
    call = fake.last("sendPhoto", 42)
    assert call.files["photo"][0] == "a.png"
    assert call.params["caption"] == "✅ Selesai! Terpakai 19 kredit."
    assert refs["file_ids"] and refs["kind"] == "image"


async def test_multiple_photos_use_media_group_then_caption(delivery):
    channel, fake = delivery
    refs = await channel.deliver(JOB, [png(), png(), png()])
    group = fake.last("sendMediaGroup", 42)
    assert [m["media"] for m in group.params["media"]] == ["attach://file0", "attach://file1", "attach://file2"]
    assert set(group.files) == {"file0", "file1", "file2"}
    assert fake.last("sendMessage", 42).text.startswith("✅ Selesai")
    assert len(refs["file_ids"]) == 3


async def test_video_and_oversized_files(delivery):
    channel, fake = delivery
    await channel.deliver(JOB, [MediaFile(b"mp4", "video/mp4", "v.mp4", "results/x/0.mp4")])
    assert fake.last("sendVideo", 42).params["supports_streaming"] is True
    big_photo = MediaFile(b"0" * (PHOTO_LIMIT + 1), "image/png", "big.png", "results/x/big.png")
    await channel.deliver(JOB, [big_photo])
    assert fake.last("sendDocument", 42).files["document"][0] == "big.png"
    huge = MediaFile(b"0" * (UPLOAD_LIMIT + 1), "video/mp4", "huge.mp4", "results/x/huge.mp4")
    refs = await channel.deliver(JOB, [huge])
    assert "https://storage.test/results/x/huge.mp4" in fake.last("sendMessage", 42).text
    assert refs["file_ids"] == []


async def test_progress_message_is_removed_on_delivery_and_edited_on_progress(delivery):
    channel, fake = delivery
    job = Job(**{**JOB.__dict__, "chat_ref": {"chat_id": 42, "progress_message_id": 9, "lang": "en"}})
    await channel.progress(job, Progress(state="running", eta_s=30))
    assert fake.last("editMessageText", 42).text == "⏳ Working on it… about 30 seconds left."
    await channel.progress(job, Progress(state="queued", queue_position=3))
    assert "position 3" in fake.last("editMessageText", 42).text
    await channel.deliver(job, [png()])
    assert fake.last("deleteMessage", 42).params["message_id"] == 9


async def test_failure_notice_with_refund(delivery):
    channel, fake = delivery
    await channel.failed(JOB, FailureReason.MODERATION, refunded=True)
    text = fake.last("sendMessage", 42).text
    assert text.startswith("🚫") and "19 kredit sudah dikembalikan" in text
    assert fake.last("sendMessage", 42).callback_data() == ["menu:home"]


async def test_blocked_user_becomes_channel_unreachable(delivery):
    channel, fake = delivery
    fake.fail_next("sendPhoto", 403, "Forbidden: bot was blocked by the user")
    with pytest.raises(ChannelUnreachable):
        await channel.deliver(JOB, [png()])
    fake.fail_next("sendMessage", 403, "Forbidden: bot was blocked by the user")
    with pytest.raises(ChannelUnreachable):
        await channel.failed(JOB, FailureReason.TIMEOUT, refunded=True)


async def test_transient_telegram_error_propagates(delivery):
    channel, fake = delivery
    fake.fail_next("sendPhoto", 502, "Bad Gateway")
    from app.infra.telegram import TelegramError

    with pytest.raises(TelegramError):
        await channel.deliver(JOB, [png()])


# ---------------------------------------------------------------------------- storage
async def test_local_storage_roundtrip_and_signed_urls(tmp_path):
    storage = LocalStorage(tmp_path, "https://bot.example.com/", b"secret")
    await storage.put("inputs/u1/a.jpg", b"jpeg", "image/jpeg")
    assert await storage.get("inputs/u1/a.jpg") == b"jpeg"
    url = await storage.url("inputs/u1/a.jpg", 60)
    assert url.startswith("https://bot.example.com/media/inputs/u1/a.jpg?exp=")
    exp = int(url.split("exp=")[1].split("&")[0])
    sig = url.split("sig=")[1]
    assert await storage.read_signed("inputs/u1/a.jpg", exp, sig) == (b"jpeg", "image/jpeg")
    assert await storage.read_signed("inputs/u1/a.jpg", exp, "0" * 64) is None
    assert await storage.read_signed("inputs/u1/other.jpg", exp, sig) is None
    await storage.delete("inputs/u1/a.jpg")
    await storage.delete("inputs/u1/a.jpg")  # idempotent
    assert await storage.read_signed("inputs/u1/a.jpg", exp, sig) is None


def test_signatures_expire_and_paths_cannot_escape(tmp_path):
    expires = int(time.time()) - 1
    assert not verify(b"k", "a", expires, sign(b"k", "a", expires))
    storage = LocalStorage(tmp_path, "", b"k")
    with pytest.raises(ValueError):
        storage.path("../../etc/passwd")


async def test_s3_storage_with_stubbed_client():
    import boto3
    from botocore.response import StreamingBody
    from botocore.stub import Stubber

    client = boto3.client("s3", region_name="auto", endpoint_url="https://acct.r2.cloudflarestorage.com",
                          aws_access_key_id="k", aws_secret_access_key="s")
    storage = S3Storage(client, "media")
    with Stubber(client) as stub:
        stub.add_response("put_object", {}, {"Bucket": "media", "Key": "k.png", "Body": b"x",
                                             "ContentType": "image/png"})
        stub.add_response("get_object", {"Body": StreamingBody(io.BytesIO(b"x"), 1)},
                          {"Bucket": "media", "Key": "k.png"})
        stub.add_response("delete_object", {}, {"Bucket": "media", "Key": "k.png"})
        await storage.put("k.png", b"x", "image/png")
        assert await storage.get("k.png") == b"x"
        await storage.delete("k.png")
        stub.assert_no_pending_responses()
    url = await storage.url("k.png", 60)
    assert url.startswith("https://acct.r2.cloudflarestorage.com/media/k.png?") and "Expires=" in url \
        or "X-Amz-Expires=60" in url
