"""Add account_label to intake_records (one profile serves many billing accounts;
output routes to the account's folder).

Revision ID: 011
Revises: 010
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa


revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None

TABLE = "intake_records"
COLUMN = "account_label"


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    if not _has_column(TABLE, COLUMN):
        op.add_column(TABLE, sa.Column(COLUMN, sa.String(256), nullable=True))
        op.create_index(f"ix_{TABLE}_{COLUMN}", TABLE, [COLUMN])


def downgrade() -> None:
    if _has_column(TABLE, COLUMN):
        op.drop_index(f"ix_{TABLE}_{COLUMN}", table_name=TABLE)
        op.drop_column(TABLE, COLUMN)
