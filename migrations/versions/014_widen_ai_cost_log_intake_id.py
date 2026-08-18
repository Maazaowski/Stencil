"""Widen ai_cost_logs.intake_id to hold synthetic ids (e.g. ``authoring-<uuid>``).

The profile-authoring flow logs AI cost rows keyed by ``authoring-<session_id>``
(46 chars), which overflowed the original String(36) column.

Revision ID: 014
Revises: 013
Create Date: 2026-06-26
"""

import sqlalchemy as sa
from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None

TABLE = "ai_cost_logs"
COLUMN = "intake_id"


def _column(table: str, column: str) -> dict | None:
    bind = op.get_bind()
    for col in sa.inspect(bind).get_columns(table):
        if col["name"] == column:
            return col
    return None


def upgrade() -> None:
    if _column(TABLE, COLUMN) is not None:
        op.alter_column(
            TABLE,
            COLUMN,
            existing_type=sa.String(36),
            type_=sa.String(64),
            existing_nullable=True,
        )


def downgrade() -> None:
    if _column(TABLE, COLUMN) is not None:
        op.alter_column(
            TABLE,
            COLUMN,
            existing_type=sa.String(64),
            type_=sa.String(36),
            existing_nullable=True,
        )
