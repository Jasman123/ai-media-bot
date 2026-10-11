"""User journeys through webhook → Redis queue → worker → router → fake Telegram, with real Postgres."""

from sqlalchemy import select, update

from app.bot.router import HandlerSpec, Router
from app.infra.models import UserRow
from tests.support import updates


async def _db_user(stack, telegram_id: int) -> UserRow:
    async with stack.sessionmaker() as session:
        return await session.scalar(select(UserRow).where(UserRow.telegram_id == telegram_id))


async def test_new_user_picks_language_then_sees_menu(stack):
    user = stack.user(8001, lang="en")
    await user.send("/start")
    assert "Pilih bahasa" in user.last_text()

    await user.press("lang:en")
    texts = user.texts()
    assert any("Hi! 👋" in t for t in texts)
    assert user.last_text() == "What would you like to create today?"
    assert "lang:en" not in user.last_markup().callback_data()
    assert "img:new" in user.last_markup().callback_data()

    db_user = await _db_user(stack, 8001)
    assert db_user.language == "en" and db_user.language_chosen

    await user.send("/start")  # returning user goes straight to the menu
    assert user.last_text() == "What would you like to create today?"


async def test_indonesian_default_and_language_switch_from_settings(stack):
    user = stack.user(8002)
    await user.send("/start")
    await user.press("lang:id")
    assert user.last_text() == "Mau buat apa hari ini?"

    await user.send("/settings")
    assert "Bahasa: Bahasa Indonesia" in user.last_markup().buttons()[0]["text"]
    await user.press("set:lang")
    await user.press("lang:en")
    assert user.last_text() == "✅ Language set to English."
    await user.send("/help")
    assert "How it works" in user.last_text()


async def test_duplicate_delivery_is_processed_once(stack):
    update = updates.message(8003, "/start", update_id=990001)
    await stack.send_update(update)
    await stack.send_update(update)
    assert len(stack.tg.sent("sendMessage", 8003)) == 1


async def test_banned_user_is_told_and_nothing_else_runs(stack):
    user = stack.user(8004)
    await user.send("/start")
    async with stack.sessionmaker() as session:
        await session.execute(update(UserRow).where(UserRow.telegram_id == 8004).values(is_banned=True))
        await session.commit()
    await user.send("/menu")
    assert "diblokir" in user.last_text()
    await user.press("menu:home")
    answer = stack.tg.last("answerCallbackQuery")
    assert answer.params["show_alert"] is True


async def test_unbuilt_menu_items_answer_coming_soon_and_text_gets_hint(stack):
    user = stack.user(8005)
    await user.send("/start")
    await user.press("lang:id")
    await user.press("zzz:new")
    assert stack.tg.last("answerCallbackQuery").params["text"] == "Fitur ini segera hadir."
    await user.send("sebuah ide acak")
    assert user.last_text().startswith("Pilih dulu")
    await user.send("/tidakada")
    assert user.last_text().startswith("Perintah tidak dikenal")


async def test_group_chatter_is_ignored(stack):
    await stack.send_update(updates.message(8006, "halo semua", chat_id=-100123, chat_type="supergroup"))
    assert stack.tg.sent("sendMessage") == []


async def test_handler_crash_sends_generic_error_and_job_still_succeeds(stack, monkeypatch):
    async def boom(ctx):
        raise RuntimeError("bug")

    router = await stack.di.get(Router)
    monkeypatch.setitem(router.commands, "help", HandlerSpec.of(boom))
    user = stack.user(8007)
    await user.send("/help")  # drain() would raise if the job itself had failed
    assert user.last_text().startswith("Maaf, terjadi kesalahan")
