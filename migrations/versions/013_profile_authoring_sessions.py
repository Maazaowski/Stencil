"""Add interactive profile-authoring sessions.

Revision ID: 013
Revises: 012
Create Date: 2026-06-26
"""

import sqlalchemy as sa
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None

SESSIONS = "profile_authoring_sessions"
INVOICES = "profile_authoring_invoices"


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    if not _has_table(SESSIONS):
        op.create_table(
            SESSIONS,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("supplier_name", sa.String(256), nullable=True),
            sa.Column("output_spec_id", sa.String(128), nullable=False, server_default="temforce.standard"),
            sa.Column("field_schema_id", sa.String(128), nullable=False, server_default="invoice.standard"),
            sa.Column("conversation", sa.JSON(), nullable=False),
            sa.Column("draft_profile", sa.JSON(), nullable=True),
            sa.Column("previews", sa.JSON(), nullable=True),
            sa.Column("finalized_profile_id", sa.String(128), nullable=True),
            sa.Column("tokens_input", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tokens_output", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("estimated_cost_usd", sa.Numeric(10, 6), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(f"ix_{SESSIONS}_status", SESSIONS, ["status"])

    if not _has_table(INVOICES):
        op.create_table(
            INVOICES,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("session_id", sa.String(36), nullable=False),
            sa.Column("filename", sa.String(512), nullable=False),
            sa.Column("extraction_status", sa.String(20), nullable=False, server_default="pending"),
            sa.Column("has_expected", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("artifact_dir", sa.String(1024), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(f"ix_{INVOICES}_session_id", INVOICES, ["session_id"])


def downgrade() -> None:
    if _has_table(INVOICES):
        op.drop_index(f"ix_{INVOICES}_session_id", table_name=INVOICES)
        op.drop_table(INVOICES)
    if _has_table(SESSIONS):
        op.drop_index(f"ix_{SESSIONS}_status", table_name=SESSIONS)
        op.drop_table(SESSIONS)
