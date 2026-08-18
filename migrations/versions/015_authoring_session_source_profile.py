"""Add ``source_profile_id`` to profile_authoring_sessions (AI edit of an existing profile).

When a session edits an existing profile, this records the source whose non-AI
config (delivery, training, fingerprint) is re-merged into the new version at
finalize time.

Revision ID: 015
Revises: 014
Create Date: 2026-06-26
"""

import sqlalchemy as sa
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None

TABLE = "profile_authoring_sessions"
COLUMN = "source_profile_id"


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    return any(col["name"] == column for col in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    if not _has_column(TABLE, COLUMN):
        op.add_column(TABLE, sa.Column(COLUMN, sa.String(128), nullable=True))


def downgrade() -> None:
    if _has_column(TABLE, COLUMN):
        op.drop_column(TABLE, COLUMN)
