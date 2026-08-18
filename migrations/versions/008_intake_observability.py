"""Intake source, celery task tracking, and processing log durations.

Revision ID: 008
Revises: 007
Create Date: 2026-06-11
"""

from alembic import op
import sqlalchemy as sa


revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None

INTAKE_TABLE = "intake_records"
LOG_TABLE = "processing_logs"


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table)}


def _existing_indexes(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    intake_cols = _existing_columns(INTAKE_TABLE)
    if "intake_source" not in intake_cols:
        op.add_column(INTAKE_TABLE, sa.Column("intake_source", sa.String(32), nullable=True))
    if "supplier_profile_id" not in intake_cols:
        op.add_column(INTAKE_TABLE, sa.Column("supplier_profile_id", sa.String(128), nullable=True))
    if "celery_task_id" not in intake_cols:
        op.add_column(INTAKE_TABLE, sa.Column("celery_task_id", sa.String(64), nullable=True))

    intake_indexes = _existing_indexes(INTAKE_TABLE)
    if "ix_intake_records_intake_source" not in intake_indexes:
        op.create_index("ix_intake_records_intake_source", INTAKE_TABLE, ["intake_source"])
    if "ix_intake_records_supplier_profile_id" not in intake_indexes:
        op.create_index("ix_intake_records_supplier_profile_id", INTAKE_TABLE, ["supplier_profile_id"])

    log_cols = _existing_columns(LOG_TABLE)
    if "duration_ms" not in log_cols:
        op.add_column(LOG_TABLE, sa.Column("duration_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    log_cols = _existing_columns(LOG_TABLE)
    if "duration_ms" in log_cols:
        op.drop_column(LOG_TABLE, "duration_ms")

    intake_indexes = _existing_indexes(INTAKE_TABLE)
    if "ix_intake_records_supplier_profile_id" in intake_indexes:
        op.drop_index("ix_intake_records_supplier_profile_id", table_name=INTAKE_TABLE)
    if "ix_intake_records_intake_source" in intake_indexes:
        op.drop_index("ix_intake_records_intake_source", table_name=INTAKE_TABLE)

    intake_cols = _existing_columns(INTAKE_TABLE)
    if "celery_task_id" in intake_cols:
        op.drop_column(INTAKE_TABLE, "celery_task_id")
    if "supplier_profile_id" in intake_cols:
        op.drop_column(INTAKE_TABLE, "supplier_profile_id")
    if "intake_source" in intake_cols:
        op.drop_column(INTAKE_TABLE, "intake_source")
