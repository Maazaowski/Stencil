"""Add model validation lifecycle fields.

Revision ID: 004
Revises: 003
Create Date: 2026-06-03
"""

from alembic import op
import sqlalchemy as sa


revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


TABLE_NAME = "extraction_models"
INDEX_NAME = "ix_extraction_models_layout_family_key"


def _existing_columns() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(TABLE_NAME)}


def _existing_indexes() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {index["name"] for index in inspector.get_indexes(TABLE_NAME)}


def _add_column_if_missing(columns: set[str], column: sa.Column) -> None:
    if column.name not in columns:
        op.add_column(TABLE_NAME, column)


def upgrade() -> None:
    columns = _existing_columns()
    _add_column_if_missing(columns, sa.Column("layout_family_key", sa.String(256), nullable=True))
    _add_column_if_missing(
        columns,
        sa.Column("allow_supplier_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    _add_column_if_missing(
        columns,
        sa.Column("validation_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column_if_missing(
        columns,
        sa.Column("validation_success_count", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column_if_missing(
        columns,
        sa.Column("validation_failure_count", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column_if_missing(
        columns,
        sa.Column("approved_after_intake_count", sa.Integer(), nullable=False, server_default="0"),
    )
    _add_column_if_missing(columns, sa.Column("last_validation_error", sa.Text(), nullable=True))
    _add_column_if_missing(columns, sa.Column("validation_intake_ids", sa.JSON(), nullable=True))

    if INDEX_NAME not in _existing_indexes():
        op.create_index(INDEX_NAME, TABLE_NAME, ["layout_family_key"])


def downgrade() -> None:
    if INDEX_NAME in _existing_indexes():
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)

    columns = _existing_columns()
    for column_name in (
        "validation_intake_ids",
        "last_validation_error",
        "approved_after_intake_count",
        "validation_failure_count",
        "validation_success_count",
        "validation_attempt_count",
        "allow_supplier_fallback",
        "layout_family_key",
    ):
        if column_name in columns:
            op.drop_column(TABLE_NAME, column_name)
