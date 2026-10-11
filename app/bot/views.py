"""Screen builders: turn data into (text, keyboard). Pure presentation, no I/O."""

from .context import Ctx
from .keyboards import btn, cb, kb


def settings_view(ctx: Ctx) -> tuple[str, dict]:
    language = ctx.t("lang.name")
    markup = kb([
        [btn(ctx.t("settings.language", language=language), cb("set", "lang"))],
        [btn(ctx.t("menu.back"), cb("menu", "home"))],
    ])
    return ctx.t("settings.title"), markup
