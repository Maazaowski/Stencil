"""Add queued profile-authoring jobs.

Revision ID: 019
Revises: 018
Create Date: 2026-07-10
"""

import sqlalchemy as sa
from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None

JOBS = "profile_authoring_jobs"


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    if _has_table(JOBS):
        return
    op.create_table(
        JOBS,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("invoice_id", sa.String(36), nullable=True),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(f"ix_{JOBS}_session_id", JOBS, ["session_id"])
    op.create_index(f"ix_{JOBS}_status", JOBS, ["status"])
    op.create_index(f"ix_{JOBS}_created_at", JOBS, ["created_at"])


def downgrade() -> None:
    if not _has_table(JOBS):
        return
    op.drop_index(f"ix_{JOBS}_created_at", table_name=JOBS)
    op.drop_index(f"ix_{JOBS}_status", table_name=JOBS)
    op.drop_index(f"ix_{JOBS}_session_id", table_name=JOBS)
    op.drop_table(JOBS)
