"""Add provider_credentials table (encrypted LLM API keys).

Stores each LLM provider's API key encrypted at rest (Fernet/ST_SECRET_KEY). Kept
separate from runtime_settings so the ciphertext never rides in the general
settings JSON blob.

Revision ID: 016
Revises: 015
Create Date: 2026-06-30
"""

import sqlalchemy as sa
from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None

TABLE = "provider_credentials"


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    if not _has_table(TABLE):
        op.create_table(
            TABLE,
            sa.Column("provider", sa.String(32), primary_key=True),
            sa.Column("key_ciphertext", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )


def downgrade() -> None:
    if _has_table(TABLE):
        op.drop_table(TABLE)
