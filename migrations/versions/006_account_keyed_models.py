"""Add account_id / account_number to extraction_models (per-account models).

Phase 1 (per-account models): extraction models are keyed by account rather than
layout fingerprint. Adds the columns; existing rows keep NULL (they are already
retired by migration 005 and will be re-learned per account).

Revision ID: 006
Revises: 005
Create Date: 2026-06-09
"""

from alembic import op
import sqlalchemy as sa


revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


TABLE = "extraction_models"


def _existing_columns() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(TABLE)}


def _existing_indexes() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {index["name"] for index in inspector.get_indexes(TABLE)}


def upgrade() -> None:
    columns = _existing_columns()
    if "account_id" not in columns:
        op.add_column(TABLE, sa.Column("account_id", sa.String(128), nullable=True))
    if "account_number" not in columns:
        op.add_column(TABLE, sa.Column("account_number", sa.String(128), nullable=True))

    indexes = _existing_indexes()
    if "ix_extraction_models_account_id" not in indexes:
        op.create_index("ix_extraction_models_account_id", TABLE, ["account_id"])
    if "ix_extraction_models_account_number" not in indexes:
        op.create_index("ix_extraction_models_account_number", TABLE, ["account_number"])


def downgrade() -> None:
    indexes = _existing_indexes()
    if "ix_extraction_models_account_number" in indexes:
        op.drop_index("ix_extraction_models_account_number", table_name=TABLE)
    if "ix_extraction_models_account_id" in indexes:
        op.drop_index("ix_extraction_models_account_id", table_name=TABLE)

    columns = _existing_columns()
    if "account_number" in columns:
        op.drop_column(TABLE, "account_number")
    if "account_id" in columns:
        op.drop_column(TABLE, "account_id")
