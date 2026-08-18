"""Profile-keyed extraction models: drop account columns, add supplier_profile_id.

Model generation is rebuilt around supplier profiles (one model per layout
profile, routed by profile_id + exact layout fingerprint). The accounts layer
is removed entirely.

- Adds indexed supplier_profile_id.
- Backfills supplier_profile_id from model_json where present; everything else
  is retired (legacy account-keyed models are not executable by the new
  interpreter anyway).
- Drops account_id / account_number.

Revision ID: 007
Revises: 006
Create Date: 2026-06-11
"""

from alembic import op
import sqlalchemy as sa


revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


TABLE = "extraction_models"


def _existing_columns() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(TABLE)}


def _existing_indexes() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {index["name"] for index in inspector.get_indexes(TABLE)}


def upgrade() -> None:
    columns = _existing_columns()
    if "supplier_profile_id" not in columns:
        op.add_column(TABLE, sa.Column("supplier_profile_id", sa.String(128), nullable=True))

    indexes = _existing_indexes()
    if "ix_extraction_models_supplier_profile_id" not in indexes:
        op.create_index("ix_extraction_models_supplier_profile_id", TABLE, ["supplier_profile_id"])

    bind = op.get_bind()
    # Backfill from model_json where the new schema field is present.
    rows = bind.execute(sa.text(f"SELECT id, model_json FROM {TABLE}")).fetchall()
    import json

    for row in rows:
        try:
            payload = row[1] if isinstance(row[1], dict) else json.loads(row[1] or "{}")
        except (TypeError, ValueError):
            payload = {}
        profile_id = payload.get("supplier_profile_id")
        if profile_id:
            bind.execute(
                sa.text(f"UPDATE {TABLE} SET supplier_profile_id = :pid WHERE id = :id"),
                {"pid": profile_id, "id": row[0]},
            )
        else:
            # Legacy account-keyed regex/template models are not executable by
            # the new interpreter — retire them.
            bind.execute(
                sa.text(f"UPDATE {TABLE} SET status = 'retired' WHERE id = :id AND status != 'retired'"),
                {"id": row[0]},
            )

    indexes = _existing_indexes()
    if "ix_extraction_models_account_id" in indexes:
        op.drop_index("ix_extraction_models_account_id", table_name=TABLE)
    if "ix_extraction_models_account_number" in indexes:
        op.drop_index("ix_extraction_models_account_number", table_name=TABLE)

    columns = _existing_columns()
    if "account_id" in columns:
        op.drop_column(TABLE, "account_id")
    if "account_number" in columns:
        op.drop_column(TABLE, "account_number")


def downgrade() -> None:
    columns = _existing_columns()
    if "account_id" not in columns:
        op.add_column(TABLE, sa.Column("account_id", sa.String(128), nullable=True))
    if "account_number" not in columns:
        op.add_column(TABLE, sa.Column("account_number", sa.String(128), nullable=True))

    indexes = _existing_indexes()
    if "ix_extraction_models_supplier_profile_id" in indexes:
        op.drop_index("ix_extraction_models_supplier_profile_id", table_name=TABLE)

    columns = _existing_columns()
    if "supplier_profile_id" in columns:
        op.drop_column(TABLE, "supplier_profile_id")
