"""/start, language, main menu, help, settings, and the fallbacks."""

from ...services.users import set_language
from ..context import Ctx
from ..keyboards import back_to_menu, btn, cb, kb, language_picker, main_menu
from ..router import Router

router = Router()


async def show_menu(ctx: Ctx, edit: bool = False) -> None:
    text = ctx.t("menu.title")
    if edit:
        await ctx.edit_or_reply(text, main_menu(ctx.t))
    else:
        await ctx.reply(text, main_menu(ctx.t))


@router.command("start")
async def start(ctx: Ctx) -> None:
    await ctx.container.state.clear(ctx.user.id)
    if not ctx.user.language_chosen:
        await ctx.reply(ctx.t("lang.pick"), language_picker(ctx.t))
        return
    await show_menu(ctx)


@router.command("menu")
async def menu_command(ctx: Ctx) -> None:
    await ctx.container.state.clear(ctx.user.id)
    await show_menu(ctx)


@router.callback("menu")
async def menu_callback(ctx: Ctx) -> None:
    await ctx.answer()
    await ctx.container.state.clear(ctx.user.id)
    await show_menu(ctx, edit=True)


@router.command("language", "bahasa")
async def language_command(ctx: Ctx) -> None:
    await ctx.reply(ctx.t("lang.pick"), language_picker(ctx.t))


@router.callback("lang")
async def language_callback(ctx: Ctx) -> None:
    chosen = ctx.container.i18n.normalize(ctx.args[0] if ctx.args else None)
    first_time = not ctx.user.language_chosen
    async with ctx.container.db() as session:
        await set_language(session, ctx.user, chosen)
        await session.commit()
    await ctx.answer(ctx.t("lang.set"))
    if first_time:
        await ctx.edit_or_reply(ctx.t("start.welcome"))
        await show_menu(ctx)
    else:
        await ctx.edit_or_reply(ctx.t("lang.set"), back_to_menu(ctx.t))


@router.command("help", "bantuan")
async def help_command(ctx: Ctx) -> None:
    await ctx.reply(ctx.t("help.text"), back_to_menu(ctx.t))


@router.callback("help")
async def help_callback(ctx: Ctx) -> None:
    await ctx.answer()
    await ctx.edit_or_reply(ctx.t("help.text"), back_to_menu(ctx.t))


def settings_view(ctx: Ctx) -> tuple[str, dict]:
    language = ctx.container.i18n.t(ctx.lang, "lang.name")
    markup = kb([
        [btn(ctx.t("settings.language", language=language), cb("set", "lang"))],
        [btn(ctx.t("menu.back"), cb("menu", "home"))],
    ])
    return ctx.t("settings.title"), markup


@router.command("settings", "pengaturan")
async def settings_command(ctx: Ctx) -> None:
    text, markup = settings_view(ctx)
    await ctx.reply(text, markup)


@router.callback("set")
async def settings_callback(ctx: Ctx) -> None:
    await ctx.answer()
    if ctx.args and ctx.args[0] == "lang":
        await ctx.edit_or_reply(ctx.t("lang.pick"), language_picker(ctx.t))
        return
    text, markup = settings_view(ctx)
    await ctx.edit_or_reply(text, markup)


@router.command("cancel", "batal")
async def cancel_command(ctx: Ctx) -> None:
    await ctx.container.state.clear(ctx.user.id)
    await ctx.reply(ctx.t("common.cancelled"))


@router.callback("cancel")
async def cancel_callback(ctx: Ctx) -> None:
    await ctx.answer()
    await ctx.container.state.clear(ctx.user.id)
    await ctx.edit_or_reply(ctx.t("common.cancelled"))


@router.on("unknown_callback")
async def unknown_callback(ctx: Ctx) -> None:
    await ctx.answer(ctx.t("common.coming_soon"))


@router.on("unknown_command")
async def unknown_command(ctx: Ctx) -> None:
    if ctx.is_private:
        await ctx.reply(ctx.t("errors.unknown_command"))


@router.on("text")
async def text_fallback(ctx: Ctx) -> None:
    await ctx.reply(ctx.t("errors.text_hint"), main_menu(ctx.t))


@router.on("photo")
async def photo_fallback(ctx: Ctx) -> None:
    await ctx.reply(ctx.t("errors.photo_hint"), main_menu(ctx.t))
