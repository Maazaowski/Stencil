"""Add evidence-driven profile discovery persistence.

Revision ID: 022
Revises: 021
Create Date: 2026-07-15
"""

import sqlalchemy as sa
from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    if "scan_metadata" not in _columns("intake_records"):
        op.add_column("intake_records", sa.Column("scan_metadata", sa.JSON(), nullable=True))
    sessions = _columns("profile_authoring_sessions")
    additions = {
        "phase": sa.Column("phase", sa.String(32), nullable=False, server_default="setup"),
        "inferred_category": sa.Column("inferred_category", sa.String(32), nullable=True),
        "category_override": sa.Column("category_override", sa.String(32), nullable=True),
        "discovery_report": sa.Column("discovery_report", sa.JSON(), nullable=True),
        "validation_summary": sa.Column("validation_summary", sa.JSON(), nullable=True),
        "engine_version": sa.Column("engine_version", sa.String(32), nullable=True),
    }
    for name, column in additions.items():
        if name not in sessions:
            op.add_column("profile_authoring_sessions", column)

    if "scan_metadata" not in _columns("profile_authoring_invoices"):
        op.add_column(
            "profile_authoring_invoices",
            sa.Column("scan_metadata", sa.JSON(), nullable=True),
        )

    inspector = sa.inspect(op.get_bind())
    if "profile_authoring_blueprints" not in inspector.get_table_names():
        op.create_table(
            "profile_authoring_blueprints",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("session_id", sa.String(36), nullable=False),
            sa.Column("invoice_id", sa.String(36), nullable=True),
            sa.Column("filename", sa.String(512), nullable=False),
            sa.Column("artifact_path", sa.String(1024), nullable=False),
            sa.Column("matching_metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(
            "ix_profile_authoring_blueprints_session_id",
            "profile_authoring_blueprints",
            ["session_id"],
        )
        op.create_index(
            "ix_profile_authoring_blueprints_invoice_id",
            "profile_authoring_blueprints",
            ["invoice_id"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "profile_authoring_blueprints" in inspector.get_table_names():
        op.drop_index(
            "ix_profile_authoring_blueprints_invoice_id",
            table_name="profile_authoring_blueprints",
        )
        op.drop_index(
            "ix_profile_authoring_blueprints_session_id",
            table_name="profile_authoring_blueprints",
        )
        op.drop_table("profile_authoring_blueprints")
    if "scan_metadata" in _columns("profile_authoring_invoices"):
        op.drop_column("profile_authoring_invoices", "scan_metadata")
    for name in reversed([
        "phase",
        "inferred_category",
        "category_override",
        "discovery_report",
        "validation_summary",
        "engine_version",
    ]):
        if name in _columns("profile_authoring_sessions"):
            op.drop_column("profile_authoring_sessions", name)
    if "scan_metadata" in _columns("intake_records"):
        op.drop_column("intake_records", "scan_metadata")
