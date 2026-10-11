"""Admin API (X-API-Key). Catalog management (A2): toggle models/providers, prices, repricing."""

from decimal import Decimal
from typing import Any

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..domain.catalog import CatalogModel
from ..domain.errors import NotFound
from ..services.catalog import CatalogService
from ..services.providers import ProviderRegistry
from .security import require_api_key

router = APIRouter(prefix="/api/admin", route_class=DishkaRoute, dependencies=[Depends(require_api_key)])


def catalog_json(model: CatalogModel) -> dict[str, Any]:
    return {
        "id": model.id, "kind": model.kind.value, "tier": model.tier, "provider": model.provider,
        "provider_model_id": model.provider_model_id,
        "capabilities": sorted(op.value for op in model.capabilities),
        "cost_model": model.cost_model.value, "unit_cost_usd": str(model.unit_cost_usd),
        "audio_unit_cost_usd": None if model.audio_unit_cost_usd is None else str(model.audio_unit_cost_usd),
        "credits": model.credits, "audio_credits": model.audio_credits, "credits_override": model.credits_override,
        "durations": list(model.durations), "aspect_ratios": list(model.aspect_ratios), "max_n": model.max_n,
        "eta_s": model.eta_s, "priority": model.priority, "fallback_id": model.fallback_id,
        "enabled": model.enabled, "notes": model.notes,
    }


class CatalogPatch(BaseModel):
    enabled: bool | None = None
    priority: int | None = None
    credits: int | None = None
    audio_credits: int | None = None
    credits_override: bool | None = None
    unit_cost_usd: Decimal | None = None
    audio_unit_cost_usd: Decimal | None = None
    fallback_id: str | None = None
    provider: str | None = None
    provider_model_id: str | None = None
    eta_s: int | None = None
    notes: str | None = None


@router.get("/catalog")
async def list_catalog(catalog: FromDishka[CatalogService], registry: FromDishka[ProviderRegistry]):
    return {"providers": registry.names, "models": [catalog_json(m) for m in await catalog.all()]}


@router.patch("/catalog/{model_id}")
async def patch_catalog(model_id: str, body: CatalogPatch, catalog: FromDishka[CatalogService]):
    changes = body.model_dump(exclude_unset=True)
    try:
        return catalog_json(await catalog.update(model_id, changes))
    except NotFound:
        raise HTTPException(status_code=404, detail="Unknown model") from None
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.post("/providers/{provider}/{action}")
async def toggle_provider(provider: str, action: str, catalog: FromDishka[CatalogService]):
    if action not in ("enable", "disable"):
        raise HTTPException(status_code=404, detail="Unknown action")
    changed = await catalog.set_provider_enabled(provider, action == "enable")
    return {"provider": provider, "enabled": action == "enable", "models": changed}


@router.post("/catalog/reprice")
async def reprice(catalog: FromDishka[CatalogService]):
    return {"models": [catalog_json(m) for m in await catalog.reprice()]}
