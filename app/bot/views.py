"""Screen builders: turn data into (text, keyboard). Pure presentation, no I/O."""

from ..services.ledger import AccountSummary
from .context import Ctx
from .formatting import format_date, format_number
from .keyboards import btn, cb, kb


def settings_view(ctx: Ctx) -> tuple[str, dict]:
    language = ctx.t("lang.name")
    markup = kb([
        [btn(ctx.t("settings.language", language=language), cb("set", "lang"))],
        [btn(ctx.t("menu.back"), cb("menu", "home"))],
    ])
    return ctx.t("settings.title"), markup


def credits_view(ctx: Ctx, summary: AccountSummary) -> tuple[str, dict]:
    balance = summary.balance
    lines = [ctx.t("credits.title"), "",
             ctx.t("credits.paid", paid=format_number(balance.paid, ctx.lang))]
    if balance.free:
        if summary.free_expires_at:
            lines.append(ctx.t("credits.free_until", free=format_number(balance.free, ctx.lang),
                               date=format_date(summary.free_expires_at, ctx.lang)))
        else:
            lines.append(ctx.t("credits.free", free=format_number(balance.free, ctx.lang)))
        lines.append(ctx.t("credits.free_note"))
    lines += ["", ctx.t("credits.total", total=format_number(balance.total, ctx.lang))]
    markup = kb([[btn(ctx.t("menu.back"), cb("menu", "home"))]])
    return "\n".join(lines), markup
