"""Add users + auth_sessions tables (login system).

Users carry bcrypt password hashes; sessions store only the SHA-256 of the
opaque cookie token, so a DB leak exposes neither passwords nor live sessions.

Revision ID: 017
Revises: 016
Create Date: 2026-07-03
"""

import sqlalchemy as sa
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    if not _has_table("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("email", sa.String(255), nullable=False, unique=True, index=True),
            sa.Column("password_hash", sa.String(255), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("last_login_at", sa.DateTime(), nullable=True),
        )
    if not _has_table("auth_sessions"):
        op.create_table(
            "auth_sessions",
            sa.Column("token_hash", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False, index=True),
        )


def downgrade() -> None:
    if _has_table("auth_sessions"):
        op.drop_table("auth_sessions")
    if _has_table("users"):
        op.drop_table("users")
