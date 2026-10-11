"""Dispatcher routing with every collaborator injected as an in-memory fake (no Postgres, Redis or HTTP)."""

import pytest

from app.bot.context import Ctx
from app.bot.dispatcher import BotConfig, Dispatcher
from app.bot.router import Router
from app.i18n import I18n
from app.services.users import UserService
from tests.support import updates
from tests.support.fake_telegram import FakeTelegram
from tests.support.fakes import FakeUnitOfWork, InMemoryUserRepository, InMemoryWizardState


class Greeter:
    """A stand-in service a handler can ask for by type."""

    def __init__(self) -> None:
        self.greeted: list[int] = []


class StubContainer:
    def __init__(self, values: dict) -> None:
        self.values = values

    async def get(self, dep_type):
        return self.values[dep_type]


@pytest.fixture
def harness():
    hits: list[tuple] = []
    router = Router()
    greeter = Greeter()

    @router.command("start")
    async def start(ctx: Ctx, g: Greeter) -> None:
        g.greeted.append(ctx.user.telegram_id)
        hits.append(("start", ctx.args))

    @router.callback("img")
    async def img(ctx: Ctx) -> None:
        hits.append(("img", ctx.args))

    @router.state("img:prompt")
    async def prompt(ctx: Ctx) -> None:
        hits.append(("state", ctx.text))

    @router.on("text")
    async def text(ctx: Ctx) -> None:
        hits.append(("text", ctx.text))

    @router.on("photo")
    async def photo(ctx: Ctx) -> None:
        hits.append(("photo", None))

    @router.on("successful_payment")
    async def paid(ctx: Ctx) -> None:
        hits.append(("paid", None))

    @router.on("pre_checkout")
    async def pre_checkout(ctx: Ctx) -> None:
        hits.append(("pre_checkout", None))

    repo, uow, state, tg = InMemoryUserRepository(), FakeUnitOfWork(), InMemoryWizardState(), FakeTelegram()
    dispatcher = Dispatcher(router, UserService(repo, uow), tg.client(), I18n.load(), state,
                            BotConfig(username="test_media_bot"), StubContainer({Greeter: greeter}))
    return dispatcher, hits, repo, uow, state, tg, greeter


async def test_routes_commands_with_injected_dependency(harness):
    dispatcher, hits, repo, uow, *_, greeter = harness
    await dispatcher.dispatch(updates.message(1, "/start ref_X"))
    assert hits == [("start", ["ref_X"])]
    assert greeter.greeted == [1]
    assert uow.commits == 1  # user registration committed by the service, not the handler


async def test_routes_callbacks_states_text_and_media(harness):
    dispatcher, hits, repo, uow, state, *_ = harness
    await dispatcher.dispatch(updates.callback(1, "img:tier:fast"))
    await dispatcher.dispatch(updates.message(1, "kucing"))
    user = await repo.get_by_telegram_id(1)
    await state.set(user.id, "img:prompt")
    await dispatcher.dispatch(updates.message(1, "kucing batik"))
    await dispatcher.dispatch(updates.message(1, None, photo_file_id="f"))
    await dispatcher.dispatch(updates.pre_checkout(1, "p", 50))
    assert hits == [("img", ["tier", "fast"]), ("text", "kucing"), ("state", "kucing batik"), ("photo", None),
                    ("pre_checkout", None)]


async def test_group_messages_only_route_commands(harness):
    dispatcher, hits, *_ = harness
    await dispatcher.dispatch(updates.message(1, "hello", chat_id=-100, chat_type="group"))
    await dispatcher.dispatch(updates.message(1, "/start@other_bot", chat_id=-100, chat_type="group"))
    await dispatcher.dispatch(updates.message(1, "/start", chat_id=-100, chat_type="group"))
    assert hits == [("start", [])]


async def test_banned_users_are_blocked_but_payments_still_processed(harness):
    dispatcher, hits, repo, *_ = harness
    await dispatcher.dispatch(updates.message(1, "/start"))
    user = await repo.get_by_telegram_id(1)
    await repo.set_banned(user.id, True, "spam")
    hits.clear()
    await dispatcher.dispatch(updates.message(1, "/start"))
    await dispatcher.dispatch(updates.successful_payment(1, "p", 50, "c1"))
    assert hits == [("paid", None)]


async def test_bots_and_senderless_updates_are_ignored(harness):
    dispatcher, hits, repo, *_ = harness
    bot_update = updates.message(1, "/start")
    bot_update["message"]["from"]["is_bot"] = True
    await dispatcher.dispatch(bot_update)
    await dispatcher.dispatch({"update_id": 1, "poll": {}})
    assert hits == [] and repo.by_id == {}


async def test_handler_exception_becomes_generic_error_message(harness):
    dispatcher, hits, *_, tg, _ = harness

    async def boom(ctx: Ctx) -> None:
        raise RuntimeError("bug")

    from app.bot.router import HandlerSpec

    dispatcher._router.commands["boom"] = HandlerSpec.of(boom)
    await dispatcher.dispatch(updates.message(5, "/boom"))
    assert tg.last("sendMessage", 5).text.startswith("Maaf, terjadi kesalahan")
