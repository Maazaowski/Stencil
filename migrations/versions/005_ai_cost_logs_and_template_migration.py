"""Add ai_cost_logs table and retire legacy regex extraction models.

Phase 1d: per-call AI cost tracking table.
Phase 4b: retire all existing regex-based extraction_models rows — they will be
re-learned as label-based ExtractionTemplates on the next AI extraction for each
layout.

Revision ID: 005
Revises: 004
Create Date: 2026-06-08
"""

from alembic import op
import sqlalchemy as sa


revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


COST_TABLE = "ai_cost_logs"
MODELS_TABLE = "extraction_models"


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return name in inspector.get_table_names()


def upgrade() -> None:
    # 1. Create the per-call AI cost tracking table.
    if not _has_table(COST_TABLE):
        op.create_table(
            COST_TABLE,
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("intake_id", sa.String(36), nullable=True),
            sa.Column("job_id", sa.String(36), nullable=True),
            sa.Column("call_type", sa.String(30), nullable=False),
            sa.Column("ai_model_name", sa.String(64), nullable=False),
            sa.Column("tokens_input", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tokens_output", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tokens_cached", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("estimated_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
            sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(20), nullable=False, server_default="success"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_ai_cost_logs_intake_id", COST_TABLE, ["intake_id"])
        op.create_index("ix_ai_cost_logs_job_id", COST_TABLE, ["job_id"])

    # 2. Retire all existing regex-based extraction models. They are replaced by
    #    label-based templates re-learned on the next AI extraction per layout.
    if _has_table(MODELS_TABLE):
        op.execute(
            sa.text(
                f"UPDATE {MODELS_TABLE} SET status = 'retired' "
                "WHERE status NOT IN ('retired')"
            )
        )


def downgrade() -> None:
    if _has_table(COST_TABLE):
        op.drop_index("ix_ai_cost_logs_job_id", table_name=COST_TABLE)
        op.drop_index("ix_ai_cost_logs_intake_id", table_name=COST_TABLE)
        op.drop_table(COST_TABLE)
    # Retiring legacy models is not reversible (we don't know prior statuses).
