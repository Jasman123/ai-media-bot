from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class CatalogRow(Base):
    __tablename__ = "model_catalog"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))
    tier: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    provider_model_id: Mapped[str] = mapped_column(String(200))
    capabilities: Mapped[list] = mapped_column(JSONB, default=list)
    cost_model: Mapped[str] = mapped_column(String(16))
    unit_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    audio_unit_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    credits: Mapped[int] = mapped_column(Integer)
    audio_credits: Mapped[int | None] = mapped_column(Integer)
    credits_override: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    durations: Mapped[list] = mapped_column(JSONB, default=list)
    aspect_ratios: Mapped[list] = mapped_column(JSONB, default=list)
    resolutions: Mapped[list] = mapped_column(JSONB, default=list)
    max_n: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    eta_s: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    fallback_id: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
