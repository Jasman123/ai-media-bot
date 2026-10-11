"""Model catalog (A2) and pricing rules (plan §5).

One catalog row per model offered. The user-facing price is attached to the **tier**: a request is priced from the
tier's primary model; fallbacks are only used if they keep the margin above the floor.
"""

import math
from dataclasses import dataclass, field, replace
from decimal import ROUND_CEILING, Decimal
from enum import Enum
from typing import Protocol

from .generation import GenerationRequest, MediaKind, Operation


class CostModel(str, Enum):
    PER_IMAGE = "per_image"
    PER_MEGAPIXEL = "per_megapixel"
    PER_SECOND = "per_second"
    PER_JOB = "per_job"


@dataclass(frozen=True)
class CatalogModel:
    id: str
    kind: MediaKind
    tier: str
    provider: str
    provider_model_id: str
    capabilities: frozenset[Operation]
    cost_model: CostModel
    unit_cost_usd: Decimal
    credits: int                          # user price per unit (image / second / job)
    audio_unit_cost_usd: Decimal | None = None
    audio_credits: int | None = None
    credits_override: bool = False        # admin-set price: repricing leaves it alone
    durations: tuple[int, ...] = ()
    aspect_ratios: tuple[str, ...] = ("1:1",)
    resolutions: tuple[str, ...] = ()
    max_n: int = 1
    eta_s: int = 30
    priority: int = 100                   # lower = preferred within a tier
    fallback_id: str | None = None
    enabled: bool = True
    notes: str = field(default="", compare=False)

    def supports(self, operation: Operation) -> bool:
        return operation in self.capabilities

    @property
    def supports_audio(self) -> bool:
        return self.audio_unit_cost_usd is not None


@dataclass(frozen=True)
class PricingPolicy:
    stars_net_usd: Decimal = Decimal("0.013")   # what we earn per Star (Bot Developer ToS §6.2.4)
    markup: Decimal = Decimal("3")
    min_margin: Decimal = Decimal("1.5")        # a fallback may not push revenue/cost below this

    def credits_for_cost(self, cost_usd: Decimal) -> int:
        """stars = ceil(cost × markup ÷ net per Star); 1 credit = 1 Star at base pack price."""
        if cost_usd <= 0:
            return 0
        value = (cost_usd * self.markup / self.stars_net_usd).to_integral_value(rounding=ROUND_CEILING)
        return max(1, int(value))


def billable_units(model: CatalogModel, req: GenerationRequest) -> Decimal:
    if model.cost_model in (CostModel.PER_SECOND,):
        return Decimal(req.duration_s or (model.durations[0] if model.durations else 5))
    if model.cost_model == CostModel.PER_JOB:
        return Decimal(1)
    return Decimal(max(1, req.n))


def megapixels(resolution: str | None) -> Decimal:
    if resolution and "x" in resolution:
        w, h = (int(v) for v in resolution.lower().split("x", 1))
        return Decimal(w * h) / Decimal(1_000_000)
    return Decimal(1)


def estimate_cost_usd(model: CatalogModel, req: GenerationRequest) -> Decimal:
    unit = model.audio_unit_cost_usd if (req.audio and model.audio_unit_cost_usd is not None) else model.unit_cost_usd
    cost = unit * billable_units(model, req)
    if model.cost_model == CostModel.PER_MEGAPIXEL:
        cost *= megapixels(req.resolution)
    return cost


def credits_for(model: CatalogModel, req: GenerationRequest) -> int:
    """User price of a request on this (primary) model."""
    per_unit = model.audio_credits if (req.audio and model.audio_credits is not None) else model.credits
    return int(math.ceil(per_unit * billable_units(model, req)))


def margin_ok(policy: PricingPolicy, credits_charged: int, fallback_cost_usd: Decimal) -> bool:
    """Revenue (credits × net per Star) must stay ≥ min_margin × provider cost."""
    if fallback_cost_usd <= 0:
        return True
    revenue = Decimal(credits_charged) * policy.stars_net_usd
    return revenue >= policy.min_margin * fallback_cost_usd


def repriced(model: CatalogModel, policy: PricingPolicy) -> CatalogModel:
    if model.credits_override:
        return model
    return replace(
        model,
        credits=policy.credits_for_cost(model.unit_cost_usd),
        audio_credits=(policy.credits_for_cost(model.audio_unit_cost_usd)
                       if model.audio_unit_cost_usd is not None else None),
    )


class CatalogRepository(Protocol):
    async def list_all(self) -> list[CatalogModel]: ...

    async def get(self, model_id: str) -> CatalogModel | None: ...

    async def insert_missing(self, models: list[CatalogModel]) -> int:
        """Insert rows whose id doesn't exist yet; never overwrite (admin edits survive deploys)."""
        ...

    async def save(self, model: CatalogModel) -> CatalogModel: ...

    async def set_provider_enabled(self, provider: str, enabled: bool) -> int: ...


@dataclass(frozen=True)
class CatalogSeed:
    """Rows shipped with the code (YAML). Inserted when missing; never overwrites admin edits."""

    models: tuple[CatalogModel, ...] = ()
