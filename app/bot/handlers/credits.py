"""💰 Kredit: balance (free vs paid, free-credit expiry). Pack purchase is added by the payments feature."""

from ...services.ledger import LedgerService
from ..context import Ctx
from ..router import Router
from ..views import credits_view

router = Router()


@router.command("saldo", "kredit", "balance", "credits")
async def balance_command(ctx: Ctx, ledger: LedgerService) -> None:
    await ctx.reply(*credits_view(ctx, await ledger.summary(ctx.user.id)))


@router.callback("cred")
async def credits_callback(ctx: Ctx, ledger: LedgerService) -> None:
    await ctx.answer()
    await ctx.edit_or_reply(*credits_view(ctx, await ledger.summary(ctx.user.id)))
