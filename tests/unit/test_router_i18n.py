import pytest

from app.bot import build_router
from app.bot.dispatcher import parse_command
from app.bot.keyboards import CALLBACK_LIMIT, cb, main_menu
from app.bot.router import HandlerSpec, Router
from app.i18n import I18n, placeholders


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
    assert parse_command(text, "test_media_bot") == expected


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


def test_handler_spec_extracts_typed_dependencies():
    class Service:
        pass

    async def handler(ctx, svc: Service, other: int) -> None: ...

    spec = HandlerSpec.of(handler)
    assert spec.deps == {"svc": Service, "other": int}


def test_handler_spec_requires_type_hints_for_dependencies():
    async def untyped(ctx, svc) -> None: ...

    async def no_ctx() -> None: ...

    with pytest.raises(TypeError):
        HandlerSpec.of(untyped)
    with pytest.raises(TypeError):
        HandlerSpec.of(no_ctx)


