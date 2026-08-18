"""Move the disk-backed config registries (supplier profiles, field schemas,
output specs) into MySQL. Tables hold the full JSON in a ``data`` column; the
committed disk JSON only seeds these on boot (import-if-not-exists).

Revision ID: 010
Revises: 009
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa


revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    if not _has_table("supplier_profiles"):
        op.create_table(
            "supplier_profiles",
            sa.Column("profile_id", sa.String(128), primary_key=True),
            sa.Column("status", sa.String(50), nullable=False, server_default="draft"),
            sa.Column("canonical_name", sa.String(256), nullable=True),
            sa.Column("data", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_supplier_profiles_status", "supplier_profiles", ["status"])
        op.create_index(
            "ix_supplier_profiles_canonical_name", "supplier_profiles", ["canonical_name"]
        )

    if not _has_table("field_schemas"):
        op.create_table(
            "field_schemas",
            sa.Column("schema_id", sa.String(128), primary_key=True),
            sa.Column("name", sa.String(256), nullable=True),
            sa.Column("data", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )

    if not _has_table("output_specs"):
        op.create_table(
            "output_specs",
            sa.Column("spec_id", sa.String(128), primary_key=True),
            sa.Column("name", sa.String(256), nullable=True),
            sa.Column("data", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )


def downgrade() -> None:
    for table in ("output_specs", "field_schemas", "supplier_profiles"):
        if _has_table(table):
            op.drop_table(table)
