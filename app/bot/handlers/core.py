"""/start, language, main menu, help, settings, cancel, and the fallbacks."""

from ...services.users import UserService
from ..context import Ctx
from ..keyboards import back_to_menu, language_picker, main_menu
from ..router import Router
from ..views import settings_view

router = Router()


async def show_menu(ctx: Ctx, edit: bool = False) -> None:
    if edit:
        await ctx.edit_or_reply(ctx.t("menu.title"), main_menu(ctx.t))
    else:
        await ctx.reply(ctx.t("menu.title"), main_menu(ctx.t))


@router.command("start")
async def start(ctx: Ctx) -> None:
    await ctx.state.clear(ctx.user.id)
    if not ctx.user.language_chosen:
        await ctx.reply(ctx.t("lang.pick"), language_picker(ctx.t))
        return
    await show_menu(ctx)


@router.command("menu")
async def menu_command(ctx: Ctx) -> None:
    await ctx.state.clear(ctx.user.id)
    await show_menu(ctx)


@router.callback("menu")
async def menu_callback(ctx: Ctx) -> None:
    await ctx.answer()
    await ctx.state.clear(ctx.user.id)
    await show_menu(ctx, edit=True)


@router.command("language", "bahasa")
async def language_command(ctx: Ctx) -> None:
    await ctx.reply(ctx.t("lang.pick"), language_picker(ctx.t))


@router.callback("lang")
async def choose_language(ctx: Ctx, users: UserService) -> None:
    first_time = not ctx.user.language_chosen
    ctx.with_user(await users.choose_language(ctx.user.id, ctx.args[0] if ctx.args else ""))
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


@router.command("settings", "pengaturan")
async def settings_command(ctx: Ctx) -> None:
    await ctx.reply(*settings_view(ctx))


@router.callback("set")
async def settings_callback(ctx: Ctx) -> None:
    await ctx.answer()
    if ctx.args and ctx.args[0] == "lang":
        await ctx.edit_or_reply(ctx.t("lang.pick"), language_picker(ctx.t))
        return
    await ctx.edit_or_reply(*settings_view(ctx))


@router.command("cancel", "batal")
async def cancel_command(ctx: Ctx) -> None:
    await ctx.state.clear(ctx.user.id)
    await ctx.reply(ctx.t("common.cancelled"))


@router.callback("cancel")
async def cancel_callback(ctx: Ctx) -> None:
    await ctx.answer()
    await ctx.state.clear(ctx.user.id)
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
