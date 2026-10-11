from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_lease", "status", "lease_until"),
        Index("ix_jobs_provider_job", "provider", "provider_job_id"),
        Index("ix_jobs_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    operation: Mapped[str] = mapped_column(String(16))
    tier: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12))
    credits_charged: Mapped[int] = mapped_column(Integer)
    free_credits_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    prompt: Mapped[str] = mapped_column(Text, default="", server_default="")
    prompt_original: Mapped[str] = mapped_column(Text, default="", server_default="")
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    input_keys: Mapped[list] = mapped_column(JSONB, default=list)
    channel: Mapped[str] = mapped_column(String(16), default="telegram", server_default="telegram")
    chat_ref: Mapped[dict] = mapped_column(JSONB, default=dict)
    provider: Mapped[str | None] = mapped_column(String(32))
    provider_job_id: Mapped[str | None] = mapped_column(String(200))
    reservation_id: Mapped[str | None] = mapped_column(String(36))
    watermark: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    cost_usd_estimate: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0, server_default="0")
    cost_usd_actual: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    result_urls: Mapped[list] = mapped_column(JSONB, default=list)
    result_mime: Mapped[str | None] = mapped_column(String(64))
    result_keys: Mapped[list] = mapped_column(JSONB, default=list)
    result_refs: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    deliver_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    poll_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(36))
    parent_job_id: Mapped[str | None] = mapped_column(String(36))
    template_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
