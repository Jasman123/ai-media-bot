"""model catalog

Revision ID: 0002_model_catalog
Revises: 0001_users
Create Date: 2026-10-11
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_model_catalog"
down_revision = "0001_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_catalog",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("tier", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_model_id", sa.String(length=200), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cost_model", sa.String(length=16), nullable=False),
        sa.Column("unit_cost_usd", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("audio_unit_cost_usd", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("audio_credits", sa.Integer(), nullable=True),
        sa.Column("credits_override", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("durations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("aspect_ratios", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resolutions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("max_n", sa.Integer(), server_default="1", nullable=False),
        sa.Column("eta_s", sa.Integer(), server_default="30", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("fallback_id", sa.String(length=64), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_catalog")),
    )
    op.create_index(op.f("ix_model_catalog_provider"), "model_catalog", ["provider"], unique=False)
    op.create_index(op.f("ix_model_catalog_tier"), "model_catalog", ["tier"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_model_catalog_tier"), table_name="model_catalog")
    op.drop_index(op.f("ix_model_catalog_provider"), table_name="model_catalog")
    op.drop_table("model_catalog")
