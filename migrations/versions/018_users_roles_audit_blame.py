"""Add user roles and blame/audit metadata.

Revision ID: 018
Revises: 017
Create Date: 2026-07-03
"""

import sqlalchemy as sa
from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def _add_column(table: str, column: sa.Column) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    _add_column("users", sa.Column("username", sa.String(255), nullable=True))
    _add_column("users", sa.Column("role", sa.String(20), nullable=False, server_default="user"))
    _add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    _add_column("users", sa.Column("created_by_user_id", sa.Integer(), nullable=True))
    _add_column("users", sa.Column("updated_by_user_id", sa.Integer(), nullable=True))
    _add_column("users", sa.Column("deleted_by_user_id", sa.Integer(), nullable=True))
    _add_column("users", sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False))
    _add_column("users", sa.Column("deleted_at", sa.DateTime(), nullable=True))

    # MySQL error 1093 forbids selecting from the UPDATE target directly;
    # wrapping the subquery in a derived table materializes it first.
    op.execute(
        "UPDATE users SET role = 'admin' "
        "WHERE id = (SELECT id FROM (SELECT MIN(id) AS id FROM users) AS first_user)"
    )

    _add_column("supplier_profiles", sa.Column("created_by_user_id", sa.Integer(), nullable=True))
    _add_column("supplier_profiles", sa.Column("updated_by_user_id", sa.Integer(), nullable=True))
    _add_column("supplier_profiles", sa.Column("deleted_by_user_id", sa.Integer(), nullable=True))
    _add_column("supplier_profiles", sa.Column("deleted_at", sa.DateTime(), nullable=True))

    _add_column("extraction_models", sa.Column("updated_by", sa.String(255), nullable=True))
    _add_column("extraction_models", sa.Column("retired_by", sa.String(255), nullable=True))
    _add_column("extraction_models", sa.Column("retired_at", sa.DateTime(), nullable=True))
    try:
        op.alter_column("extraction_models", "created_by", type_=sa.String(255), existing_type=sa.String(64))
        op.alter_column("extraction_models", "approved_by", type_=sa.String(255), existing_type=sa.String(64))
    except Exception:
        pass

    if not _has_table("audit_events"):
        op.create_table(
            "audit_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("entity_type", sa.String(50), nullable=False, index=True),
            sa.Column("entity_id", sa.String(255), nullable=False, index=True),
            sa.Column("action", sa.String(50), nullable=False, index=True),
            sa.Column("actor_user_id", sa.Integer(), nullable=True),
            sa.Column("actor_email", sa.String(255), nullable=True),
            sa.Column("actor_username", sa.String(255), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False, index=True),
        )


def downgrade() -> None:
    if _has_table("audit_events"):
        op.drop_table("audit_events")
    for table, cols in {
        "extraction_models": ["retired_at", "retired_by", "updated_by"],
        "supplier_profiles": ["deleted_at", "deleted_by_user_id", "updated_by_user_id", "created_by_user_id"],
        "users": [
            "deleted_at",
            "updated_at",
            "deleted_by_user_id",
            "updated_by_user_id",
            "created_by_user_id",
            "is_active",
            "role",
            "username",
        ],
    }.items():
        existing = _columns(table)
        for col in cols:
            if col in existing:
                op.drop_column(table, col)
