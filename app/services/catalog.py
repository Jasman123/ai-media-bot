"""Catalog use cases (A2): what can be offered, at what price, and which model actually serves a request."""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from ..domain.catalog import (
    CatalogModel,
    CatalogRepository,
    CatalogSeed,
    PricingPolicy,
    credits_for,
    estimate_cost_usd,
    repriced,
)
from ..domain.errors import NoModelAvailable, NotFound
from ..domain.generation import GenerationRequest, Operation
from ..domain.ports import UnitOfWork
from .providers import ProviderRegistry

EDITABLE_FIELDS = {"enabled", "priority", "credits", "audio_credits", "unit_cost_usd", "audio_unit_cost_usd",
                   "fallback_id", "provider", "provider_model_id", "eta_s", "credits_override", "notes"}


@dataclass(frozen=True)
class Quote:
    model: CatalogModel          # the tier's primary model: the price is attached to the tier
    credits: int
    cost_usd: Decimal


class CatalogService:
    def __init__(self, repo: CatalogRepository, registry: ProviderRegistry, policy: PricingPolicy,
                 uow: UnitOfWork):
        self._repo = repo
        self._registry = registry
        self._policy = policy
        self._uow = uow

    # ------------------------------------------------------------------ reads
    async def all(self) -> list[CatalogModel]:
        return sorted(await self._repo.list_all(), key=lambda m: (m.kind.value, m.tier, m.priority, m.id))

    def _usable(self, model: CatalogModel, operation: Operation | None = None) -> bool:
        return (model.enabled and self._registry.has(model.provider)
                and (operation is None or model.supports(operation)))

    async def tiers(self, operation: Operation) -> list[CatalogModel]:
        """The primary model of every tier that can serve `operation`, cheapest tier first.

        The wizard builds its options (durations, aspect ratios, audio) from these rows, so swapping the model
        behind a tier never breaks the UI (plan §3).
        """
        best: dict[str, CatalogModel] = {}
        for model in await self._repo.list_all():
            if not self._usable(model, operation):
                continue
            current = best.get(model.tier)
            if current is None or (model.priority, model.id) < (current.priority, current.id):
                best[model.tier] = model
        return sorted(best.values(), key=lambda m: (m.credits, m.tier))

    async def primary(self, tier: str, operation: Operation) -> CatalogModel:
        for model in await self.tiers(operation):
            if model.tier == tier:
                return model
        raise NoModelAvailable(f"no model for tier {tier!r} / {operation.value}")

    async def chain(self, model_id: str, operation: Operation) -> list[CatalogModel]:
        """Primary model then its fallbacks, skipping disabled/unregistered/incapable ones. Cycle-safe."""
        models = {m.id: m for m in await self._repo.list_all()}
        chain, seen, current = [], set(), models.get(model_id)
        while current is not None and current.id not in seen:
            seen.add(current.id)
            if self._usable(current, operation):
                chain.append(current)
            current = models.get(current.fallback_id) if current.fallback_id else None
        return chain

    async def quote(self, tier: str, req: GenerationRequest) -> Quote:
        model = await self.primary(tier, req.operation)
        req = replace(req, provider_model_id=model.provider_model_id)
        return Quote(model=model, credits=credits_for(model, req), cost_usd=estimate_cost_usd(model, req))

    def price(self, model: CatalogModel, req: GenerationRequest) -> int:
        return credits_for(model, req)

    def cost(self, model: CatalogModel, req: GenerationRequest) -> Decimal:
        return estimate_cost_usd(model, req)

    # ------------------------------------------------------------------ admin
    async def sync_seed(self, seed: CatalogSeed) -> int:
        inserted = await self._repo.insert_missing([repriced(m, self._policy) for m in seed.models])
        await self._uow.commit()
        return inserted

    async def update(self, model_id: str, changes: dict[str, Any]) -> CatalogModel:
        unknown = set(changes) - EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"not editable: {sorted(unknown)}")
        model = await self._repo.get(model_id)
        if model is None:
            raise NotFound(f"catalog model {model_id!r}")
        if "credits" in changes or "audio_credits" in changes:
            changes = {**changes, "credits_override": changes.get("credits_override", True)}
        for key in ("unit_cost_usd", "audio_unit_cost_usd"):
            if changes.get(key) is not None:
                changes[key] = Decimal(str(changes[key]))
        updated = replace(model, **changes)
        if not updated.credits_override and ({"unit_cost_usd", "audio_unit_cost_usd", "credits_override"}
                                             & set(changes)):
            updated = repriced(updated, self._policy)
        saved = await self._repo.save(updated)
        await self._uow.commit()
        return saved

    async def set_provider_enabled(self, provider: str, enabled: bool) -> int:
        count = await self._repo.set_provider_enabled(provider, enabled)
        await self._uow.commit()
        return count

    async def pause(self, model_id: str) -> None:
        """Called when a provider reports QuotaExhausted for this model."""
        model = await self._repo.get(model_id)
        if model is not None and model.enabled:
            await self._repo.save(replace(model, enabled=False, notes=(model.notes + " [auto-paused: quota]").strip()))
            await self._uow.commit()

    async def reprice(self) -> list[CatalogModel]:
        """Recompute credits from unit_cost_usd for every row without a manual override (plan §5)."""
        out = []
        for model in await self._repo.list_all():
            new = repriced(model, self._policy)
            if new != model:
                new = await self._repo.save(new)
            out.append(new)
        await self._uow.commit()
        return out
