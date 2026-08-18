"""Add prompt workbench registry.

Revision ID: 020
Revises: 019
Create Date: 2026-07-13
"""

import sqlalchemy as sa
from alembic import op

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("prompt_versions"):
        op.create_table(
            "prompt_versions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("family", sa.String(64), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=True),
            sa.Column("parent_version_id", sa.String(36), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("title", sa.String(256), nullable=False),
            sa.Column("hypothesis", sa.Text(), nullable=True),
            sa.Column("change_summary", sa.Text(), nullable=True),
            sa.Column("bundle", sa.JSON(), nullable=False),
            sa.Column("content_sha256", sa.String(64), nullable=True),
            sa.Column("approval_override_reason", sa.Text(), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
            sa.Column("committed_by_user_id", sa.Integer(), nullable=True),
            sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("committed_at", sa.DateTime(), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("retired_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("family", "version_number", name="uq_prompt_versions_family_number"),
        )
        op.create_index("ix_prompt_versions_family", "prompt_versions", ["family"])
        op.create_index("ix_prompt_versions_status", "prompt_versions", ["status"])
        op.create_index("ix_prompt_versions_parent", "prompt_versions", ["parent_version_id"])
        op.create_index("ix_prompt_versions_sha", "prompt_versions", ["content_sha256"])
    if not _has_table("prompt_activations"):
        op.create_table(
            "prompt_activations",
            sa.Column("family", sa.String(64), primary_key=True),
            sa.Column("active_version_id", sa.String(36), nullable=False),
            sa.Column("previous_version_id", sa.String(36), nullable=True),
            sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("activated_by_user_id", sa.Integer(), nullable=True),
            sa.Column("activated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_prompt_activations_active", "prompt_activations", ["active_version_id"])
    if not _has_table("prompt_qualifications"):
        op.create_table(
            "prompt_qualifications",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("prompt_version_id", sa.String(36), nullable=False),
            sa.Column("baseline_version_id", sa.String(36), nullable=False),
            sa.Column("model", sa.String(64), nullable=False),
            sa.Column("reasoning_effort", sa.String(20), nullable=True),
            sa.Column("repetitions", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("scope", sa.String(20), nullable=False, server_default="full"),
            sa.Column("case_ids", sa.JSON(), nullable=True),
            sa.Column("candidate_run_id", sa.String(64), nullable=True),
            sa.Column("baseline_run_id", sa.String(64), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
            sa.Column("gate_result", sa.JSON(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_by_user_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_prompt_qualifications_version", "prompt_qualifications", ["prompt_version_id"])
        op.create_index("ix_prompt_qualifications_status", "prompt_qualifications", ["status"])


def downgrade() -> None:
    for table in ("prompt_qualifications", "prompt_activations", "prompt_versions"):
        if _has_table(table):
            op.drop_table(table)
