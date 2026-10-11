"""generation jobs

Revision ID: 0004_jobs
Revises: 0003_ledger
Create Date: 2026-10-11
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_jobs"
down_revision = "0003_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("tier", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("credits_charged", sa.Integer(), nullable=False),
        sa.Column("free_credits_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("prompt", sa.Text(), server_default="", nullable=False),
        sa.Column("prompt_original", sa.Text(), server_default="", nullable=False),
        sa.Column("params", jsonb, nullable=False),
        sa.Column("input_keys", jsonb, nullable=False),
        sa.Column("channel", sa.String(length=16), server_default="telegram", nullable=False),
        sa.Column("chat_ref", jsonb, nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("provider_job_id", sa.String(length=200), nullable=True),
        sa.Column("reservation_id", sa.String(length=36), nullable=True),
        sa.Column("watermark", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("cost_usd_estimate", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False),
        sa.Column("cost_usd_actual", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("result_urls", jsonb, nullable=False),
        sa.Column("result_mime", sa.String(length=64), nullable=True),
        sa.Column("result_keys", jsonb, nullable=False),
        sa.Column("result_refs", jsonb, nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("deliver_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("poll_seq", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=36), nullable=True),
        sa.Column("parent_job_id", sa.String(length=36), nullable=True),
        sa.Column("template_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_jobs_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index("ix_jobs_status_lease", "jobs", ["status", "lease_until"], unique=False)
    op.create_index("ix_jobs_provider_job", "jobs", ["provider", "provider_job_id"], unique=False)
    op.create_index("ix_jobs_user_created", "jobs", ["user_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_jobs_user_created", table_name="jobs")
    op.drop_index("ix_jobs_provider_job", table_name="jobs")
    op.drop_index("ix_jobs_status_lease", table_name="jobs")
    op.drop_table("jobs")
