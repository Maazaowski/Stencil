"""Initial schema — intake records, extraction jobs, extraction models, processing logs.

Revision ID: 001
Revises: None
Create Date: 2026-05-20

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intake_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("original_pdf_path", sa.String(1024), nullable=False),
        sa.Column("archive_pdf_path", sa.String(1024), nullable=True),
        sa.Column("file_size_bytes", sa.Integer, default=0),
        sa.Column("page_count", sa.Integer, default=0),
        sa.Column("status", sa.String(50), default="received", index=True),
        sa.Column("layout_fingerprint", sa.String(128), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column("error_message", sa.Text, nullable=True),
    )

    op.create_table(
        "extraction_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("intake_id", sa.String(36), index=True),
        sa.Column("extraction_path", sa.String(30)),
        sa.Column("extraction_model_id", sa.String(128), nullable=True),
        sa.Column("supplier_profile_id", sa.String(128), nullable=True),
        sa.Column("supplier_name", sa.String(256), nullable=True),
        sa.Column("output_type", sa.String(30), default="standard"),
        sa.Column("status", sa.String(50), default="pending", index=True),
        sa.Column("ai_model_name", sa.String(64), nullable=True),
        sa.Column("tokens_input", sa.Integer, default=0),
        sa.Column("tokens_output", sa.Integer, default=0),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 6), default=0),
        sa.Column("extraction_duration_ms", sa.Integer, default=0),
        sa.Column("overall_confidence", sa.Float, default=0),
        sa.Column("line_item_count", sa.Integer, default=0),
        sa.Column("is_reconciled", sa.Boolean, nullable=True),
        sa.Column("reconciliation_variance", sa.Float, nullable=True),
        sa.Column("canonical_json_path", sa.String(1024), nullable=True),
        sa.Column("xlsx_output_path", sa.String(1024), nullable=True),
        sa.Column("manifest_path", sa.String(1024), nullable=True),
        sa.Column("exception_path", sa.String(1024), nullable=True),
        sa.Column("warnings", sa.JSON, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("completed_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "extraction_models",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("version", sa.Integer, default=1),
        sa.Column("supplier", sa.String(256), index=True),
        sa.Column("output_type", sa.String(30), default="standard"),
        sa.Column("layout_fingerprint", sa.String(128), index=True),
        sa.Column("status", sa.String(20), default="draft", index=True),
        sa.Column("model_json", sa.JSON),
        sa.Column("confidence", sa.Float, default=0),
        sa.Column("times_used", sa.Integer, default=0),
        sa.Column("last_used_at", sa.DateTime, nullable=True),
        sa.Column("success_rate", sa.Float, default=0),
        sa.Column("created_by", sa.String(64), default="ai_extraction"),
        sa.Column("created_from_intake", sa.String(36), nullable=True),
        sa.Column("approved_by", sa.String(64), nullable=True),
        sa.Column("approved_at", sa.DateTime, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now()),
    )

    op.create_table(
        "processing_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("intake_id", sa.String(36), index=True),
        sa.Column("job_id", sa.String(36), nullable=True),
        sa.Column("step", sa.String(50)),
        sa.Column("status", sa.String(30)),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("details", sa.JSON, nullable=True),
        sa.Column("timestamp", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("processing_logs")
    op.drop_table("extraction_models")
    op.drop_table("extraction_jobs")
    op.drop_table("intake_records")
