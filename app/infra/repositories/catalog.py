from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...domain.catalog import CatalogModel, CostModel
from ...domain.generation import MediaKind, Operation
from ..models.catalog import CatalogRow


def to_model(row: CatalogRow) -> CatalogModel:
    return CatalogModel(
        id=row.id,
        kind=MediaKind(row.kind),
        tier=row.tier,
        provider=row.provider,
        provider_model_id=row.provider_model_id,
        capabilities=frozenset(Operation(c) for c in row.capabilities),
        cost_model=CostModel(row.cost_model),
        unit_cost_usd=row.unit_cost_usd,
        audio_unit_cost_usd=row.audio_unit_cost_usd,
        credits=row.credits,
        audio_credits=row.audio_credits,
        credits_override=row.credits_override,
        durations=tuple(row.durations),
        aspect_ratios=tuple(row.aspect_ratios),
        resolutions=tuple(row.resolutions),
        max_n=row.max_n,
        eta_s=row.eta_s,
        priority=row.priority,
        fallback_id=row.fallback_id,
        enabled=row.enabled,
        notes=row.notes,
    )


def to_values(model: CatalogModel) -> dict:
    return dict(
        id=model.id,
        kind=model.kind.value,
        tier=model.tier,
        provider=model.provider,
        provider_model_id=model.provider_model_id,
        capabilities=sorted(op.value for op in model.capabilities),
        cost_model=model.cost_model.value,
        unit_cost_usd=model.unit_cost_usd,
        audio_unit_cost_usd=model.audio_unit_cost_usd,
        credits=model.credits,
        audio_credits=model.audio_credits,
        credits_override=model.credits_override,
        durations=list(model.durations),
        aspect_ratios=list(model.aspect_ratios),
        resolutions=list(model.resolutions),
        max_n=model.max_n,
        eta_s=model.eta_s,
        priority=model.priority,
        fallback_id=model.fallback_id,
        enabled=model.enabled,
        notes=model.notes,
    )


class SqlCatalogRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_all(self) -> list[CatalogModel]:
        rows = await self._session.scalars(select(CatalogRow).execution_options(populate_existing=True))
        return [to_model(r) for r in rows]

    async def get(self, model_id: str) -> CatalogModel | None:
        row = await self._session.get(CatalogRow, model_id, populate_existing=True)
        return to_model(row) if row else None

    async def insert_missing(self, models: list[CatalogModel]) -> int:
        if not models:
            return 0
        result = await self._session.execute(
            insert(CatalogRow).values([to_values(m) for m in models])
            .on_conflict_do_nothing(index_elements=[CatalogRow.id]).returning(CatalogRow.id)
        )
        return len(result.all())

    async def save(self, model: CatalogModel) -> CatalogModel:
        values = to_values(model)
        row = await self._session.scalar(
            insert(CatalogRow).values(values)
            .on_conflict_do_update(index_elements=[CatalogRow.id],
                                   set_={k: v for k, v in values.items() if k != "id"})
            .returning(CatalogRow).execution_options(populate_existing=True)
        )
        return to_model(row)

    async def set_provider_enabled(self, provider: str, enabled: bool) -> int:
        result = await self._session.execute(
            update(CatalogRow).where(CatalogRow.provider == provider).values(enabled=enabled)
            .returning(CatalogRow.id)
        )
        return len(result.all())
