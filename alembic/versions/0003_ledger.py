"""credit ledger (append-only) and reservations

Revision ID: 0003_ledger
Revises: 0002_model_catalog
Create Date: 2026-10-11
"""

import sqlalchemy as sa

from alembic import op

revision = "0003_ledger"
down_revision = "0002_model_catalog"
branch_labels = None
depends_on = None

# Two statements, executed separately (asyncpg prepares one command at a time).
APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION ledger_entries_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'ledger_entries is append-only (% blocked)', TG_OP;
END;
$$ LANGUAGE plpgsql
"""
APPEND_ONLY_TRIGGER = """
CREATE TRIGGER ledger_entries_append_only
BEFORE UPDATE OR DELETE ON ledger_entries
FOR EACH ROW EXECUTE FUNCTION ledger_entries_append_only()
"""


def upgrade() -> None:
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("bucket", sa.String(length=4), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("reservation_id", sa.String(length=36), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("payment_id", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_ledger_entries_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_entries")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_ledger_entries_idempotency_key")),
    )
    op.create_index("ix_ledger_entries_user_bucket", "ledger_entries", ["user_id", "bucket"], unique=False)
    op.create_index(op.f("ix_ledger_entries_reservation_id"), "ledger_entries", ["reservation_id"], unique=False)
    op.create_index(op.f("ix_ledger_entries_job_id"), "ledger_entries", ["job_id"], unique=False)
    op.execute(APPEND_ONLY_FUNCTION)
    op.execute(APPEND_ONLY_TRIGGER)

    op.create_table(
        "credit_reservations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("free_amount", sa.Integer(), nullable=False),
        sa.Column("paid_amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_credit_reservations_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credit_reservations")),
    )
    op.create_index(op.f("ix_credit_reservations_user_id"), "credit_reservations", ["user_id"], unique=False)
    op.create_index(op.f("ix_credit_reservations_status"), "credit_reservations", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_credit_reservations_status"), table_name="credit_reservations")
    op.drop_index(op.f("ix_credit_reservations_user_id"), table_name="credit_reservations")
    op.drop_table("credit_reservations")
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_append_only ON ledger_entries")
    op.execute("DROP FUNCTION IF EXISTS ledger_entries_append_only()")
    op.drop_index(op.f("ix_ledger_entries_job_id"), table_name="ledger_entries")
    op.drop_index(op.f("ix_ledger_entries_reservation_id"), table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_user_bucket", table_name="ledger_entries")
    op.drop_table("ledger_entries")
