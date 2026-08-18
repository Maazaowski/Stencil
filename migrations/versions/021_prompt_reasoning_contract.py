"""Persist prompt qualification reasoning contracts.

Revision ID: 021
Revises: 020
Create Date: 2026-07-13
"""

import sqlalchemy as sa
from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "prompt_qualifications" not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns("prompt_qualifications")}


def upgrade() -> None:
    columns = _columns()
    if "reasoning_effort_requested" not in columns:
        op.add_column(
            "prompt_qualifications",
            sa.Column("reasoning_effort_requested", sa.String(20), nullable=True),
        )
    if "reasoning_effort_effective" not in columns:
        op.add_column(
            "prompt_qualifications",
            sa.Column("reasoning_effort_effective", sa.String(20), nullable=True),
        )


def downgrade() -> None:
    columns = _columns()
    if "reasoning_effort_effective" in columns:
        op.drop_column("prompt_qualifications", "reasoning_effort_effective")
    if "reasoning_effort_requested" in columns:
        op.drop_column("prompt_qualifications", "reasoning_effort_requested")
