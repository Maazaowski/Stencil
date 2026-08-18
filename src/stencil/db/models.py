"""SQLAlchemy database models for intake tracking, extraction jobs, extraction models, and audit logging."""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def generate_uuid() -> str:
    return str(uuid.uuid4())


class IntakeRecord(Base):
    __tablename__ = "intake_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    original_filename: Mapped[str] = mapped_column(String(512))
    original_pdf_path: Mapped[str] = mapped_column(String(1024))
    archive_pdf_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    scan_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="received", index=True)
    layout_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    intake_source: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    supplier_profile_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    account_label: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExtractionJob(Base):
    __tablename__ = "extraction_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    intake_id: Mapped[str] = mapped_column(String(36), index=True)
    extraction_path: Mapped[str] = mapped_column(String(30))
    extraction_model_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    supplier_profile_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    supplier_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    output_type: Mapped[str] = mapped_column(String(30), default="standard")
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)

    ai_model_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens_input: Mapped[int] = mapped_column(Integer, default=0)
    tokens_output: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0)
    extraction_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    overall_confidence: Mapped[float] = mapped_column(Float, default=0)

    line_item_count: Mapped[int] = mapped_column(Integer, default=0)
    is_reconciled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reconciliation_variance: Mapped[float | None] = mapped_column(Float, nullable=True)

    canonical_json_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    xlsx_output_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    manifest_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    exception_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    warnings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ExtractionModelRecord(Base):
    """Tracks AI-generated extraction models in the database."""

    __tablename__ = "extraction_models"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    supplier: Mapped[str] = mapped_column(String(256), index=True)
    output_type: Mapped[str] = mapped_column(String(30), default="standard")
    # Routing key: one model per supplier profile (= one per layout variant).
    supplier_profile_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    layout_fingerprint: Mapped[str] = mapped_column(String(128), index=True)
    layout_family_key: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="candidate", index=True)

    model_json: Mapped[dict] = mapped_column(JSON)

    confidence: Mapped[float] = mapped_column(Float, default=0)
    allow_supplier_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    validation_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    validation_success_count: Mapped[int] = mapped_column(Integer, default=0)
    validation_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    approved_after_intake_count: Mapped[int] = mapped_column(Integer, default=0)
    last_validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_intake_ids: Mapped[list] = mapped_column(JSON, default=list)
    times_used: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    success_rate: Mapped[float] = mapped_column(Float, default=0)

    created_by: Mapped[str] = mapped_column(String(255), default="ai_extraction")
    created_from_intake: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retired_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ProcessingLog(Base):
    """Append-only audit log for every processing step."""

    __tablename__ = "processing_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    intake_id: Mapped[str] = mapped_column(String(36), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    step: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30))
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ModelTrainingRun(Base):
    """One model-training run (its own pipeline, separate from invoice processing).

    Created when the operator clicks Train; updated as the run extracts ground
    truth, authors the model, and validates it — so the workbench can show live
    progress (poll-based) and a training history per model.
    """

    __tablename__ = "model_training_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    model_id: Mapped[str] = mapped_column(String(128), index=True)
    supplier_profile_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(20), default="running", index=True)  # running|success|failed
    step: Mapped[str | None] = mapped_column(String(40), nullable=True)  # extracting|authoring|validating|done
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_steps: Mapped[int] = mapped_column(Integer, default=0)
    total_steps: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # per-invoice + result summary
    tokens_input: Mapped[int] = mapped_column(Integer, default=0)
    tokens_output: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SupplierProfileRecord(Base):
    """A supplier profile (extraction config). The full slim ``SupplierProfile``
    JSON lives in ``data``; ``status``/``canonical_name`` are denormalized for the
    active-profile and by-supplier lookups. The DB is the source of truth; disk
    JSON only seeds it on boot."""

    __tablename__ = "supplier_profiles"

    profile_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(50), default="draft", index=True)
    canonical_name: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    data: Mapped[dict] = mapped_column(JSON)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    deleted_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AuditEvent(Base):
    """Append-only actor history for user-visible configuration changes."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[str] = mapped_column(String(255), index=True)
    action: Mapped[str] = mapped_column(String(50), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class FieldSchemaRecord(Base):
    """A field schema (extraction contract). Full JSON in ``data``."""

    __tablename__ = "field_schemas"

    schema_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class OutputSpecRecord(Base):
    """An output spec (deliverable columns). Full JSON in ``data``."""

    __tablename__ = "output_specs"

    spec_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class RuntimeSettingRecord(Base):
    """Persisted mutable runtime setting shared by backend, worker, and watcher."""

    __tablename__ = "runtime_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ProviderCredential(Base):
    """An LLM provider API key, encrypted at rest (Fernet/ST_SECRET_KEY).

    Kept in its own table — not the runtime-settings JSON blob — so the ciphertext
    never rides along with the general settings dict that is loaded everywhere. The
    plaintext key is never stored or returned by the API.
    """

    __tablename__ = "provider_credentials"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    key_ciphertext: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ProfileAuthoringSession(Base):
    """An interactive AI profile-authoring session.

    The user uploads sample invoices and chats with the assistant, which drafts a
    ``SupplierProfile``. The conversation and the current draft live here (queryable
    so the UI can re-render on reload); the heavy per-invoice extraction artifacts
    live as files under ``work_dir/authoring/{session_id}/``. The session ends when
    the user finalizes it into a real (draft-status) profile.
    """

    __tablename__ = "profile_authoring_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active|running|finalized
    supplier_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    output_spec_id: Mapped[str] = mapped_column(String(128), default="temforce.standard")
    field_schema_id: Mapped[str] = mapped_column(String(128), default="invoice.standard")
    phase: Mapped[str] = mapped_column(String(32), default="setup")
    inferred_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    category_override: Mapped[str | None] = mapped_column(String(32), nullable=True)
    discovery_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    engine_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Ordered chat history: [{role, content, ...}]. Current authored draft profile JSON.
    conversation: Mapped[list] = mapped_column(JSON, default=list)
    draft_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Most recent per-invoice previews/diffs, keyed by invoice id (for reload).
    previews: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Set when editing an existing profile with AI: the source whose non-AI config
    # (delivery, training, fingerprint) is re-merged when finalizing a new version.
    source_profile_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    finalized_profile_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tokens_input: Mapped[int] = mapped_column(Integer, default=0)
    tokens_output: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ProfileAuthoringInvoice(Base):
    """One sample invoice uploaded to an authoring session. The extracted canonical
    document, layout text, and optional parsed expected-output rows are persisted as
    files under ``work_dir/authoring/{session_id}/{id}/`` and referenced here."""

    __tablename__ = "profile_authoring_invoices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    extraction_status: Mapped[str] = mapped_column(String(20), default="pending")  # uploaded|pending|done|error
    has_expected: Mapped[bool] = mapped_column(Boolean, default=False)
    artifact_dir: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    scan_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ProfileAuthoringBlueprint(Base):
    """Paired or standalone historical blueprint used as authoring evidence."""

    __tablename__ = "profile_authoring_blueprints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    invoice_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(512))
    artifact_path: Mapped[str] = mapped_column(String(1024))
    matching_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ProfileAuthoringJob(Base):
    """A queued/running/done unit of work for one profile-authoring session."""

    __tablename__ = "profile_authoring_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # turn|reextract
    # Job id is also the Celery task id so the API can revoke in-flight AI calls.
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued|running|done|error|cancelled
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    result_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, server_default=func.now(), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, server_default=func.now(), onupdate=datetime.now)


class AICostLog(Base):
    """Per-call AI cost tracking.  One row per OpenAI API call."""

    __tablename__ = "ai_cost_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Wide enough for synthetic ids like ``authoring-<uuid>`` (46 chars) used by
    # the profile-authoring flow, not just bare 36-char intake UUIDs.
    intake_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    call_type: Mapped[str] = mapped_column(String(30))
    ai_model_name: Mapped[str] = mapped_column(String(64))
    tokens_input: Mapped[int] = mapped_column(Integer, default=0)
    tokens_output: Mapped[int] = mapped_column(Integer, default=0)
    tokens_cached: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="success")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class User(Base):
    """Login user. Password stored as a bcrypt hash only."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="user", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    deleted_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuthSession(Base):
    """Server-side login session. Only the SHA-256 of the cookie token is stored."""

    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
