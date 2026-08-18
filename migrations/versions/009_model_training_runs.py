"""Model training runs (workbench training pipeline, separate from invoices).

Revision ID: 009
Revises: 008
Create Date: 2026-06-16
"""

from alembic import op
import sqlalchemy as sa


revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None

TABLE = "model_training_runs"


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    if _has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("supplier_profile_id", sa.String(128), nullable=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="running"),
        sa.Column("step", sa.String(40), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("completed_steps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_steps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("tokens_input", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_output", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index(f"ix_{TABLE}_model_id", TABLE, ["model_id"])
    op.create_index(f"ix_{TABLE}_supplier_profile_id", TABLE, ["supplier_profile_id"])
    op.create_index(f"ix_{TABLE}_state", TABLE, ["state"])


def downgrade() -> None:
    if _has_table(TABLE):
        op.drop_table(TABLE)
