import pytest

from app.bot import build_router
from app.bot.context import Ctx
from app.bot.keyboards import CALLBACK_LIMIT, cb, main_menu
from app.bot.router import Router, _parse_command, _route
from app.i18n import I18n, placeholders
from app.models import User


@pytest.fixture(scope="module")
def i18n() -> I18n:
    return I18n.load()


def test_catalogs_have_identical_keys(i18n):
    assert set(i18n.catalogs["id"]) == set(i18n.catalogs["en"])


def test_catalog_placeholders_match_between_languages(i18n):
    for key, template in i18n.catalogs["id"].items():
        assert placeholders(template) == placeholders(i18n.catalogs["en"][key]), key


def test_translation_fallbacks(i18n):
    assert i18n.t("id", "menu.image") == "🖼 Buat Gambar"
    assert i18n.t("en", "menu.image") == "🖼 Create Image"
    assert i18n.t("fr", "menu.image") == "🖼 Buat Gambar"  # unknown language -> default (ID)
    assert i18n.t("en", "no.such.key") == "no.such.key"
    assert i18n.t("en", "settings.language", language="English") == "🌐 Language: English"


def test_callback_data_limit():
    assert cb("img", "tier", "fast") == "img:tier:fast"
    with pytest.raises(ValueError):
        cb("x" * CALLBACK_LIMIT, "y")


def test_main_menu_buttons_fit_and_are_namespaced(i18n):
    markup = main_menu(lambda k: i18n.t("id", k))
    data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert len(data) == 8
    assert all(len(d.encode()) <= CALLBACK_LIMIT and ":" in d for d in data)


@pytest.mark.parametrize("text,expected", [
    ("/start", ("start", [])),
    ("/start ref_ABC", ("start", ["ref_ABC"])),
    ("/menu@test_media_bot", ("menu", [])),
    ("/menu@other_bot", None),
    ("hello", None),
])
def test_parse_command(text, expected):
    assert _parse_command(text, "test_media_bot") == expected


def test_include_rejects_duplicates():
    a, b = Router(), Router()

    @a.command("x")
    async def one(ctx): ...

    @b.command("x")
    async def two(ctx): ...

    with pytest.raises(ValueError):
        a.include(b)


def test_on_rejects_unknown_kind():
    with pytest.raises(ValueError):
        Router().on("nonsense")


def test_build_router_registers_core_handlers():
    router = build_router()
    assert {"start", "menu", "help", "settings"} <= set(router.commands)
    assert {"menu", "lang", "help", "set"} <= set(router.callbacks)


class _State:
    def __init__(self, step=None):
        self.step = step

    async def get(self, user_id):
        return {"step": self.step, "data": {}}


class _Container:
    def __init__(self, i18n, step=None):
        self.i18n = i18n
        self.state = _State(step)

        class S:
            bot_username = "test_media_bot"

        self.settings = S()


def _ctx(i18n, step=None, banned=False):
    user = User(id=1, telegram_id=1, language="id", is_banned=banned)
    return Ctx(container=_Container(i18n, step), update={}, user=user)


async def _run(update, i18n, step=None, banned=False):
    hits = []
    router = Router()

    @router.command("start")
    async def start(ctx):
        hits.append(("start", ctx.args))

    @router.callback("img")
    async def img(ctx):
        hits.append(("img", ctx.args))

    @router.state("img:prompt")
    async def prompt(ctx):
        hits.append(("state", ctx.text))

    @router.on("text")
    async def text(ctx):
        hits.append(("text", ctx.text))

    @router.on("photo")
    async def photo(ctx):
        hits.append(("photo", None))

    @router.on("successful_payment")
    async def paid(ctx):
        hits.append(("paid", None))

    await _route(_ctx(i18n, step, banned), router, update)
    return hits


async def test_route_command_callback_state_and_media(i18n):
    from tests.support import updates

    assert await _run(updates.message(1, "/start ref_X"), i18n) == [("start", ["ref_X"])]
    assert await _run(updates.callback(1, "img:tier:fast"), i18n) == [("img", ["tier", "fast"])]
    assert await _run(updates.message(1, "kucing"), i18n) == [("text", "kucing")]
    assert await _run(updates.message(1, "kucing"), i18n, step="img:prompt") == [("state", "kucing")]
    assert await _run(updates.message(1, None, photo_file_id="f"), i18n) == [("photo", None)]


async def test_group_messages_only_route_commands(i18n):
    from tests.support import updates

    assert await _run(updates.message(1, "hello", chat_id=-100, chat_type="group"), i18n) == []
    assert await _run(updates.message(1, "/start", chat_id=-100, chat_type="group"), i18n) == [("start", [])]


async def test_banned_users_still_get_payments_processed(i18n):
    from tests.support import updates

    assert await _run(updates.successful_payment(1, "p", 50, "c1"), i18n, banned=True) == [("paid", None)]
