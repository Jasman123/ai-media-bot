"""Load the shipped model catalog from YAML (app/infra/seed/catalog.yaml)."""

from decimal import Decimal
from pathlib import Path

import yaml

from ..domain.catalog import CatalogModel, CatalogSeed, CostModel
from ..domain.generation import MediaKind, Operation

DEFAULT_SEED = Path(__file__).parent / "seed" / "catalog.yaml"


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def parse_model(raw: dict) -> CatalogModel:
    return CatalogModel(
        id=raw["id"],
        kind=MediaKind(raw["kind"]),
        tier=raw["tier"],
        provider=raw["provider"],
        provider_model_id=raw["provider_model_id"],
        capabilities=frozenset(Operation(c) for c in raw["capabilities"]),
        cost_model=CostModel(raw["cost_model"]),
        unit_cost_usd=_dec(raw["unit_cost_usd"]),
        audio_unit_cost_usd=_dec(raw.get("audio_unit_cost_usd")),
        credits=int(raw.get("credits", 0)),            # 0 → computed from cost by the pricing policy
        audio_credits=raw.get("audio_credits"),
        credits_override="credits" in raw,
        durations=tuple(raw.get("durations", ())),
        aspect_ratios=tuple(raw.get("aspect_ratios", ("1:1",))),
        resolutions=tuple(raw.get("resolutions", ())),
        max_n=int(raw.get("max_n", 1)),
        eta_s=int(raw.get("eta_s", 30)),
        priority=int(raw.get("priority", 100)),
        fallback_id=raw.get("fallback_id"),
        enabled=bool(raw.get("enabled", True)),
        notes=raw.get("notes", ""),
    )


def load_catalog_seed(path: Path = DEFAULT_SEED) -> CatalogSeed:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return CatalogSeed(models=tuple(parse_model(raw) for raw in data.get("models", [])))
