"""Invoice/intake API endpoints."""

import math
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from stencil import runtime_settings
from stencil.api.deps import AdminUser, DbSession, Pagination
from stencil.api.schemas import (
    ExtractionJobResponse,
    ExtractionPreviewResponse,
    IntakeDetailResponse,
    IntakeResponse,
    InvoiceListItem,
    InvoiceListResponse,
    MessageResponse,
    ProcessingLogResponse,
)
from stencil.api.uploads import stream_upload_to, validate_upload_filename
from stencil.audit import append_event
from stencil.config import settings
from stencil.db import crud
from stencil.tasks.worker import continue_pipeline_task

logger = structlog.get_logger()

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("", response_model=InvoiceListResponse)
def list_invoices(
    db: DbSession,
    pagination: Pagination,
    status: str | None = Query(None),
    supplier: str | None = Query(None),
    suppliers: str | None = Query(None, description="Comma-separated exact supplier names"),
    search: str | None = Query(None),
    output_type: str | None = Query(None),
    extraction_path: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    intake_source: str | None = Query(None),
    fingerprint: str | None = Query(None, description="Exact layout fingerprint (for training-set pickers)"),
):
    supplier_list = None
    if suppliers:
        supplier_list = [s.strip() for s in suppliers.split(",") if s.strip()]

    items, total, status_counts, latest_jobs = crud.list_intakes(
        db,
        page=pagination.page,
        per_page=pagination.per_page,
        status=status,
        supplier=supplier if not supplier_list else None,
        suppliers=supplier_list,
        search=search,
        output_type=output_type,
        extraction_path=extraction_path,
        date_from=date_from,
        date_to=date_to,
        intake_source=intake_source,
        fingerprint=fingerprint,
    )

    enriched = []
    for intake in items:
        item = InvoiceListItem.model_validate(intake)
        job = latest_jobs.get(intake.id)
        if job is not None:
            item.supplier_name = job.supplier_name
            item.output_type = job.output_type
            item.extraction_path = job.extraction_path
            item.overall_confidence = job.overall_confidence
            item.is_reconciled = job.is_reconciled
            item.reconciliation_variance = job.reconciliation_variance
            item.job_status = job.status
            item.started_at = job.started_at
            item.completed_at = job.completed_at
        enriched.append(item)

    return InvoiceListResponse(
        items=enriched,
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=max(1, math.ceil(total / pagination.per_page)),
        status_counts=status_counts,
    )


@router.get("/facets/suppliers")
def supplier_facets(db: DbSession):
    return {"suppliers": crud.list_distinct_supplier_names(db)}


@router.delete("/purge", response_model=MessageResponse)
def purge_all_invoices(db: DbSession, admin: AdminUser):
    """Dev-only: delete all intakes, related DB rows, and work_dir artifacts.

    Admin-only AND debug-only: ``debug`` is a runtime setting any signed-in user
    could otherwise flip, so the role check is what actually protects the data.
    """
    if not bool(runtime_settings.runtime_value("debug")):
        raise HTTPException(status_code=403, detail="Purge is only available when ST_DEBUG=true")

    from stencil.intake.cleanup import purge_work_dir_artifacts

    deleted = crud.purge_all_intakes(db)
    dirs_removed = purge_work_dir_artifacts()
    logger.warning("invoices.purged", deleted_intakes=deleted, dirs_removed=dirs_removed)
    append_event(
        db,
        entity_type="intake_records",
        entity_id="*",
        action="purged",
        actor=admin,
        metadata={"deleted_intakes": deleted, "dirs_removed": dirs_removed},
    )
    return MessageResponse(
        message=f"Purged {deleted} intake(s) and {dirs_removed} work directory folder(s).",
    )


@router.get("/{intake_id}", response_model=IntakeDetailResponse)
def get_invoice(db: DbSession, intake_id: str):
    record = crud.get_intake(db, intake_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Intake record not found")

    jobs = crud.get_jobs_for_intake(db, intake_id)
    logs = crud.get_processing_logs(db, intake_id)

    resp = IntakeDetailResponse.model_validate(record)
    resp.jobs = [ExtractionJobResponse.model_validate(j) for j in jobs]
    resp.logs = [ProcessingLogResponse.model_validate(log) for log in logs]
    return resp


@router.get("/{intake_id}/jobs", response_model=list[ExtractionJobResponse])
def get_invoice_jobs(db: DbSession, intake_id: str):
    record = crud.get_intake(db, intake_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Intake record not found")
    jobs = crud.get_jobs_for_intake(db, intake_id)
    return [ExtractionJobResponse.model_validate(j) for j in jobs]


@router.get("/{intake_id}/logs", response_model=list[ProcessingLogResponse])
def get_invoice_logs(db: DbSession, intake_id: str):
    record = crud.get_intake(db, intake_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Intake record not found")
    logs = crud.get_processing_logs(db, intake_id)
    return [ProcessingLogResponse.model_validate(log) for log in logs]


@router.post("/upload", response_model=IntakeResponse)
async def upload_invoice(db: DbSession, file: UploadFile):
    """Upload a PDF invoice and start processing in the background.

    Returns the intake record immediately with status='processing'.
    The pipeline runs in a background thread and publishes real-time events
    via Redis pub/sub — connect to /api/v1/pipeline/live/{intake_id}
    or poll GET /api/v1/invoices/{intake_id} to watch progress.
    """
    safe_name = validate_upload_filename(file, allowed_suffixes=(".pdf",))

    # Streamed to the inbound directory with a size cap — reading the whole body
    # into memory first let one oversized upload exhaust the API process.
    dest = settings.inbound_dir / f"{uuid4().hex}_{safe_name}"
    size = await stream_upload_to(file, dest)

    logger.info("upload.saved", filename=safe_name, path=str(dest), size=size)

    # Create the intake record synchronously so we can return it
    from stencil.intake.service import process_new_pdf

    intake_id = process_new_pdf(
        db,
        dest,
        intake_source="upload",
        original_filename=safe_name,
    )

    # Kick off the pipeline in the worker so API restarts do not kill processing.
    async_result = continue_pipeline_task.delay(intake_id)
    crud.set_intake_celery_task(db, intake_id, async_result.id)
    logger.info("upload.pipeline_queued", intake_id=intake_id, task_id=async_result.id)

    record = crud.get_intake(db, intake_id)
    if record is None:
        raise HTTPException(status_code=500, detail="Intake record not created")
    return IntakeResponse.model_validate(record)


@router.get("/{intake_id}/model-review/{filename}")
def download_model_review_file(intake_id: str, filename: str):
    """Download AI vs model comparison artifacts from a training/validation run."""
    allowed_files = {
        "ai_output.xlsx",
        "model_output.xlsx",
        "model_output.json",
        "training_report.json",
        "execution_error.txt",
    }
    safe = Path(filename)
    if safe.name != filename or safe.suffix not in (".xlsx", ".json"):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if filename not in allowed_files:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid filename. Allowed: {', '.join(sorted(allowed_files))}",
        )

    file_path = (settings.completed_dir / intake_id / "model_review" / filename).resolve()
    base = (settings.completed_dir / intake_id / "model_review").resolve()
    if not str(file_path).startswith(str(base)):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Review file not found")

    return FileResponse(path=str(file_path), filename=filename)


@router.get("/{intake_id}/output/{filename}")
def download_output_file(intake_id: str, filename: str):
    allowed_json = {
        "canonical_invoice.json",
        "extraction_log.json",
        "manifest.json",
    }
    safe = Path(filename)
    if safe.name != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if filename.endswith(".xlsx"):
        pass  # any XLSX in the intake output folder (PDF-derived name)
    elif filename in allowed_json:
        pass
    else:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid filename. Allowed: any .xlsx output file, or "
                f"{', '.join(sorted(allowed_json))}"
            ),
        )

    base = (settings.completed_dir / intake_id).resolve()
    file_path = (base / filename).resolve()
    if not str(file_path).startswith(str(base)):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Output file not found")

    return FileResponse(path=str(file_path), filename=filename)


@router.get("/{intake_id}/preview", response_model=ExtractionPreviewResponse)
def preview_invoice_output(db: DbSession, intake_id: str):
    """Tabular preview of a processed invoice's deliverable, read from the
    persisted ``canonical_invoice.json`` and rendered through the same OutputSpec
    the XLSX uses — so the preview matches the delivered file exactly."""
    import json

    from stencil.output.preview import build_preview
    from stencil.profiles.loader import get_profile
    from stencil.specs.loader import default_output_spec, resolve_output_spec
    from stencil.validation.schema import ExtractedDocument

    record = crud.get_intake(db, intake_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Intake record not found")

    base = (settings.completed_dir / intake_id).resolve()
    canonical_path = (base / "canonical_invoice.json").resolve()
    if not str(canonical_path).startswith(str(base)) or not canonical_path.exists():
        raise HTTPException(status_code=404, detail="No extracted output to preview yet")

    try:
        data = json.loads(canonical_path.read_text(encoding="utf-8"))
        invoice = ExtractedDocument.model_validate(data)
    except Exception as exc:
        logger.error("invoice.preview.load_failed", intake_id=intake_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Could not read extracted output") from exc

    profile = get_profile(record.supplier_profile_id) if record.supplier_profile_id else None
    spec = resolve_output_spec(profile) if profile else default_output_spec()

    payload = build_preview(invoice, spec)
    payload["extraction_path"] = invoice.metadata.extraction_path.value
    payload["warnings"] = list(invoice.warnings or [])
    return payload


@router.post("/{intake_id}/reject", response_model=MessageResponse)
def reject_and_regenerate(db: DbSession, intake_id: str):
    """Reject a model-extracted invoice and regenerate via AI.

    Retires the extraction model that produced bad output, then re-queues
    the PDF through the pipeline. With the model retired, the pipeline
    falls through to AI extraction and generates a new model.
    """
    import shutil

    from stencil.models.registry import retire_model

    record = crud.get_intake(db, intake_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Intake record not found")
    if record.status != "completed":
        raise HTTPException(status_code=400, detail="Only completed invoices can be rejected")

    # Find which model was used
    jobs = crud.get_jobs_for_intake(db, intake_id)
    retired_model_id = None
    for job in jobs:
        if job.extraction_model_id and job.extraction_path in ("model", "model_fallback_ai"):
            if retire_model(db, job.extraction_model_id):
                retired_model_id = job.extraction_model_id
                logger.info("reject.model_retired", intake_id=intake_id,
                            model_id=retired_model_id)
            break

    # Ensure the PDF is in the processing directory for re-run
    processing_dir = settings.processing_dir / intake_id
    processing_pdf = processing_dir / "original.pdf"
    if not processing_pdf.exists():
        # Recover from archive
        archive_pdf = settings.archive_dir / intake_id / "original.pdf"
        if not archive_pdf.exists():
            raise HTTPException(status_code=404, detail="Original PDF not found")
        processing_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive_pdf, processing_pdf)

    # Re-queue for processing (model is retired, so AI path will be used)
    crud.update_intake_status(db, intake_id, "processing", error_message=None)
    crud.log_processing_step(
        db, intake_id=intake_id, step="rejection", status="completed",
        message=f"Extraction rejected by user. Retired model: {retired_model_id or 'none'}. "
                f"Re-queuing for AI extraction.",
    )
    async_result = continue_pipeline_task.delay(intake_id)
    crud.set_intake_celery_task(db, intake_id, async_result.id)

    msg = f"Invoice {intake_id} rejected and re-queued for AI extraction."
    if retired_model_id:
        msg += f" Model {retired_model_id} retired."
    return MessageResponse(message=msg)


@router.post("/{intake_id}/cancel", response_model=MessageResponse)
def cancel_invoice(db: DbSession, intake_id: str):
    """Cancel in-flight pipeline processing for an intake."""
    from stencil.pipeline.cancellation import request_cancel
    from stencil.pipeline.events import publish_failed
    from stencil.tasks.worker import app

    record = crud.get_intake(db, intake_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Intake record not found")
    if record.status not in ("processing", "received"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel intake with status '{record.status}'",
        )

    request_cancel(intake_id)
    if record.celery_task_id:
        app.control.revoke(record.celery_task_id, terminate=True)

    crud.update_intake_status(db, intake_id, "failed", error_message="Cancelled by user")
    crud.log_processing_step(
        db, intake_id=intake_id, step="pipeline", status="cancelled",
        message="Cancelled by user",
    )
    publish_failed(intake_id, "Cancelled by user")
    logger.info("invoice.cancelled", intake_id=intake_id, task_id=record.celery_task_id)
    return MessageResponse(message=f"Intake {intake_id} cancelled.")


@router.delete("/{intake_id}", response_model=MessageResponse)
def delete_invoice(db: DbSession, intake_id: str):
    if not crud.delete_intake(db, intake_id):
        raise HTTPException(status_code=404, detail="Intake record not found")
    return MessageResponse(message=f"Intake {intake_id} deleted")
