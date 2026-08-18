"""Pydantic response schemas for API endpoints."""

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, computed_field, field_serializer

from stencil.api.serialization import serialize_utc_datetime


class PaginatedResponse[T](BaseModel):
    items: list[T]
    total: int
    page: int
    per_page: int
    pages: int


# ── Output preview (shared across processed-invoice / profile / model surfaces) ──


class PreviewColumn(BaseModel):
    header: str
    number_format: str | None = None


class PreviewLabelValue(BaseModel):
    label: str
    value: Any = None


class ExtractionPreviewResponse(BaseModel):
    """The deliverable preview: columns + rows mirror the XLSX exactly."""

    columns: list[PreviewColumn] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    line_item_count: int = 0
    header_fields: list[PreviewLabelValue] = Field(default_factory=list)
    totals: list[PreviewLabelValue] = Field(default_factory=list)
    reconciliation: dict[str, Any] | None = None
    # Set on ad-hoc previews (profile/model) to describe how it was produced.
    extraction_path: str | None = None
    warnings: list[str] = Field(default_factory=list)
    # Which tax strategy delivered EXT_TAX: per_line / allocated / rate / none.
    tax_method: str | None = None
    # Deterministic notes-vs-profile-field contradictions (extraction/instructions.py).
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    ignored_notes: list[str] = Field(default_factory=list)


class IntakeResponse(BaseModel):
    id: str
    original_filename: str
    original_pdf_path: str
    archive_pdf_path: str | None = None
    file_size_bytes: int = 0
    page_count: int = 0
    status: str = "received"
    layout_fingerprint: str | None = None
    intake_source: str | None = None
    supplier_profile_id: str | None = None
    celery_task_id: str | None = None
    created_at: datetime
    updated_at: datetime
    error_message: str | None = None

    model_config = {"from_attributes": True}

    @field_serializer("created_at", "updated_at")
    def serialize_datetimes(self, value: datetime) -> str:
        return serialize_utc_datetime(value) or ""


class InvoiceListItem(IntakeResponse):
    """Intake enriched with fields from its latest extraction job."""

    supplier_name: str | None = None
    output_type: str | None = None
    extraction_path: str | None = None
    overall_confidence: float | None = None
    # Reconciliation is the deterministic signal; confidence is stamped 1.0 by
    # the default extraction path and measures nothing. The UI shows these.
    is_reconciled: bool | None = None
    reconciliation_variance: float | None = None
    job_status: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @field_serializer("started_at", "completed_at")
    def serialize_job_datetimes(self, value: datetime | None) -> str | None:
        return serialize_utc_datetime(value)


class InvoiceListResponse(PaginatedResponse[InvoiceListItem]):
    # Counts per intake status, honoring every active filter except `status`
    # so the UI's status chips reflect what each selection would return.
    status_counts: dict[str, int] = Field(default_factory=dict)


class ExtractionJobResponse(BaseModel):
    id: str
    intake_id: str
    extraction_path: str
    extraction_model_id: str | None = None
    supplier_profile_id: str | None = None
    supplier_name: str | None = None
    output_type: str = "standard"
    status: str = "pending"
    ai_model_name: str | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    estimated_cost_usd: float = 0
    extraction_duration_ms: int = 0
    overall_confidence: float = 0
    line_item_count: int = 0
    is_reconciled: bool | None = None
    reconciliation_variance: float | None = None
    warnings: dict | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    xlsx_output_path: str | None = Field(default=None, exclude=True)

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def xlsx_output_filename(self) -> str | None:
        if not self.xlsx_output_path:
            return None
        return Path(self.xlsx_output_path).name

    @field_serializer("started_at", "completed_at", "created_at")
    def serialize_datetimes(self, value: datetime | None) -> str | None:
        return serialize_utc_datetime(value)


class ProcessingLogResponse(BaseModel):
    id: int
    intake_id: str
    job_id: str | None = None
    step: str
    status: str
    message: str | None = None
    details: dict | None = None
    duration_ms: int | None = None
    timestamp: datetime

    model_config = {"from_attributes": True}

    @field_serializer("timestamp")
    def serialize_timestamp(self, value: datetime) -> str:
        return serialize_utc_datetime(value) or ""


class ExtractionModelResponse(BaseModel):
    id: str
    version: int = 1
    supplier: str
    output_type: str = "standard"
    supplier_profile_id: str | None = None
    layout_fingerprint: str
    layout_family_key: str | None = None
    status: str = "candidate"
    model_json: dict
    confidence: float = 0
    allow_supplier_fallback: bool = False
    validation_attempt_count: int = 0
    validation_success_count: int = 0
    validation_failure_count: int = 0
    approved_after_intake_count: int = 0
    last_validation_error: str | None = None
    validation_intake_ids: list[str] | None = None
    times_used: int = 0
    last_used_at: datetime | None = None
    success_rate: float = 0
    created_by: str = "ai_extraction"
    created_from_intake: str | None = None
    updated_by: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    retired_by: str | None = None
    retired_at: datetime | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IntakeDetailResponse(IntakeResponse):
    jobs: list[ExtractionJobResponse] = Field(default_factory=list)
    logs: list[ProcessingLogResponse] = Field(default_factory=list)


class DashboardStats(BaseModel):
    total_invoices: int = 0
    completed_invoices: int = 0
    completed_with_warnings: int = 0
    failed_invoices: int = 0
    pending_exceptions: int = 0
    # success_rate = share of scored jobs that RECONCILED (accuracy).
    # completion_rate = share of invoices that finished the pipeline (throughput).
    success_rate: float = 0.0
    completion_rate: float = 0.0
    reconciled_jobs: int = 0
    scored_jobs: int = 0
    total_ai_cost: float = 0.0
    model_training_cost: float = 0.0
    extraction_cost: float = 0.0
    total_models: int = 0
    approved_models: int = 0
    model_path_count: int = 0
    ai_path_count: int = 0


class VolumeDataPoint(BaseModel):
    date: str
    count: int


class CostDataPoint(BaseModel):
    date: str
    cost: float
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_cached: int = 0


class MonthlyStats(BaseModel):
    month: str
    total_cost: float = 0.0
    extraction_cost: float = 0.0
    classification_cost: float = 0.0
    training_cost: float = 0.0
    tokens_input: int = 0
    tokens_output: int = 0
    ai_calls: int = 0
    invoices_processed: int = 0
    invoices_completed: int = 0
    invoices_failed: int = 0
    ai_path_count: int = 0
    model_path_count: int = 0


class MonthlyCostPoint(BaseModel):
    month: str
    cost: float = 0.0


class CostByModelPoint(BaseModel):
    model: str
    cost: float = 0.0
    calls: int = 0
    tokens: int = 0


class CostBySupplierPoint(BaseModel):
    supplier_profile_id: str | None = None
    account_label: str | None = None
    cost: float = 0.0
    calls: int = 0


class ExceptionResponse(BaseModel):
    intake_id: str
    reason: str
    message: str
    details: dict = Field(default_factory=dict)
    timestamp: str
    original_filename: str | None = None
    status: str = "received"
    has_partial_extraction: bool = False
    has_original_pdf: bool = False


class MessageResponse(BaseModel):
    message: str
    success: bool = True
