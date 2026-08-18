"""Database CRUD operations for all models."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import structlog
from sqlalchemy import delete, extract, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from stencil.db.models import (
    AICostLog,
    ExtractionJob,
    ExtractionModelRecord,
    IntakeRecord,
    ModelTrainingRun,
    ProcessingLog,
    ProfileAuthoringBlueprint,
    ProfileAuthoringInvoice,
    ProfileAuthoringJob,
    ProfileAuthoringSession,
)

logger = structlog.get_logger()

SaveExtractionModelAction = Literal[
    "created",
    "updated_candidate",
    "kept_existing_approved",
]


@dataclass(frozen=True)
class SaveExtractionModelResult:
    record: ExtractionModelRecord
    action: SaveExtractionModelAction


# --- IntakeRecord ---


def create_intake(db: Session, *, original_filename: str, original_pdf_path: str,
                  archive_pdf_path: str | None = None, file_size_bytes: int = 0,
                  page_count: int = 0, intake_source: str | None = None,
                  supplier_profile_id: str | None = None,
                  account_label: str | None = None) -> IntakeRecord:
    record = IntakeRecord(
        original_filename=original_filename,
        original_pdf_path=original_pdf_path,
        archive_pdf_path=archive_pdf_path,
        file_size_bytes=file_size_bytes,
        page_count=page_count,
        intake_source=intake_source,
        supplier_profile_id=supplier_profile_id,
        account_label=account_label,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_intake(db: Session, intake_id: str) -> IntakeRecord | None:
    return db.get(IntakeRecord, intake_id)


def update_intake_status(db: Session, intake_id: str, status: str,
                         error_message: str | None = None) -> IntakeRecord | None:
    record = db.get(IntakeRecord, intake_id)
    if record is None:
        return None
    record.status = status
    if error_message is not None:
        record.error_message = error_message
    db.commit()
    db.refresh(record)
    return record


def update_intake_fingerprint(db: Session, intake_id: str,
                              fingerprint: str) -> IntakeRecord | None:
    record = db.get(IntakeRecord, intake_id)
    if record is None:
        return None
    record.layout_fingerprint = fingerprint
    db.commit()
    db.refresh(record)
    return record


def update_intake_scan_metadata(
    db: Session, intake_id: str, scan_metadata: dict,
) -> IntakeRecord | None:
    record = db.get(IntakeRecord, intake_id)
    if record is None:
        return None
    record.scan_metadata = scan_metadata
    db.commit()
    db.refresh(record)
    return record


def set_intake_celery_task(db: Session, intake_id: str, task_id: str) -> IntakeRecord | None:
    record = db.get(IntakeRecord, intake_id)
    if record is None:
        return None
    record.celery_task_id = task_id
    db.commit()
    db.refresh(record)
    return record


# --- ExtractionJob ---


def create_extraction_job(db: Session, *, intake_id: str, extraction_path: str,
                          extraction_model_id: str | None = None,
                          supplier_profile_id: str | None = None,
                          supplier_name: str | None = None,
                          output_type: str = "standard") -> ExtractionJob:
    job = ExtractionJob(
        intake_id=intake_id,
        extraction_path=extraction_path,
        extraction_model_id=extraction_model_id,
        supplier_profile_id=supplier_profile_id,
        supplier_name=supplier_name,
        output_type=output_type,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_extraction_job(db: Session, job_id: str) -> ExtractionJob | None:
    return db.get(ExtractionJob, job_id)


def update_extraction_job(db: Session, job_id: str, **kwargs) -> ExtractionJob | None:
    job = db.get(ExtractionJob, job_id)
    if job is None:
        return None
    for key, value in kwargs.items():
        if hasattr(job, key):
            setattr(job, key, value)
    db.commit()
    db.refresh(job)
    return job


def get_jobs_for_intake(db: Session, intake_id: str) -> list[ExtractionJob]:
    stmt = select(ExtractionJob).where(ExtractionJob.intake_id == intake_id)
    return list(db.scalars(stmt).all())


# --- ExtractionModelRecord ---


def save_extraction_model(db: Session, *, model_id: str, version: int, supplier: str,
                          output_type: str, layout_fingerprint: str,
                          layout_family_key: str | None = None,
                          supplier_profile_id: str | None = None,
                          model_json: dict, confidence: float = 0,
                          created_by: str = "ai_authoring",
                          created_from_intake: str | None = None,
                          notes: str | None = None) -> SaveExtractionModelResult:
    existing = db.get(ExtractionModelRecord, model_id)
    if existing is not None:
        if existing.status == "approved":
            return SaveExtractionModelResult(existing, "kept_existing_approved")

        # For retired, failed, or candidate models, overwrite with the new version.
        # This handles the retrain flow: old model was retired, the AI authors
        # a replacement with the same ID (same profile).
        model_json = dict(model_json)
        model_json["status"] = "candidate"
        model_json["layout_family_key"] = layout_family_key
        existing.version = version
        existing.supplier = supplier
        existing.output_type = output_type
        existing.supplier_profile_id = supplier_profile_id
        existing.layout_fingerprint = layout_fingerprint
        existing.layout_family_key = layout_family_key
        existing.status = "candidate"
        existing.model_json = model_json
        existing.confidence = confidence
        existing.allow_supplier_fallback = False
        existing.validation_attempt_count = 0
        existing.validation_success_count = 0
        existing.validation_failure_count = 0
        existing.approved_after_intake_count = 0
        existing.last_validation_error = None
        existing.validation_intake_ids = []
        existing.created_by = created_by
        existing.created_from_intake = created_from_intake
        existing.notes = notes
        db.commit()
        db.refresh(existing)
        return SaveExtractionModelResult(existing, "updated_candidate")

    model_json = dict(model_json)
    model_json["status"] = "candidate"
    model_json["layout_family_key"] = layout_family_key
    record = ExtractionModelRecord(
        id=model_id,
        version=version,
        supplier=supplier,
        output_type=output_type,
        supplier_profile_id=supplier_profile_id,
        layout_fingerprint=layout_fingerprint,
        layout_family_key=layout_family_key,
        status="candidate",
        model_json=model_json,
        confidence=confidence,
        allow_supplier_fallback=False,
        validation_intake_ids=[],
        created_by=created_by,
        created_from_intake=created_from_intake,
        notes=notes,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return SaveExtractionModelResult(record, "created")


def find_model_by_profile(db: Session, profile_id: str,
                          status: str | None = None) -> ExtractionModelRecord | None:
    """Find the model owned by a supplier profile (highest version first).

    A profile has exactly one approved model but may also have candidate
    clone(s) being retrained, so routing is by supplier_profile_id + status —
    NOT the primary key (a promoted clone has a versioned id, and the original
    may be retired). status, when given, filters (e.g. only 'approved').
    """
    stmt = select(ExtractionModelRecord).where(
        ExtractionModelRecord.supplier_profile_id == profile_id
    )
    if status is not None:
        stmt = stmt.where(ExtractionModelRecord.status == status)
    stmt = stmt.order_by(
        ExtractionModelRecord.version.desc(),
        ExtractionModelRecord.created_at.desc(),
    )
    record = db.scalars(stmt).first()
    if record is not None:
        return record
    # Legacy fallback: older rows used PK == profile_id without supplier_profile_id.
    record = db.get(ExtractionModelRecord, profile_id)
    if record is None:
        return None
    if status is not None and record.status != status:
        return None
    return record


def list_models_by_profile(db: Session, profile_id: str) -> list[ExtractionModelRecord]:
    """Every model row owned by a profile (canonical + clones), newest first."""
    stmt = (
        select(ExtractionModelRecord)
        .where(ExtractionModelRecord.supplier_profile_id == profile_id)
        .order_by(ExtractionModelRecord.version.desc(), ExtractionModelRecord.created_at.desc())
    )
    return list(db.scalars(stmt).all())


def max_model_version_for_profile(db: Session, profile_id: str) -> int:
    """Highest version number among a profile's models (0 if none)."""
    stmt = select(func.max(ExtractionModelRecord.version)).where(
        ExtractionModelRecord.supplier_profile_id == profile_id
    )
    return db.scalar(stmt) or 0


def get_extraction_model(db: Session, model_id: str) -> ExtractionModelRecord | None:
    return db.get(ExtractionModelRecord, model_id)


def increment_model_usage(db: Session, model_id: str) -> None:
    record = db.get(ExtractionModelRecord, model_id)
    if record is None:
        return
    record.times_used += 1
    record.last_used_at = datetime.now()
    db.commit()


def update_model_rules(
    db: Session,
    model_id: str,
    model_json: dict,
    *,
    updated_by: str | None = None,
) -> ExtractionModelRecord | None:
    """Replace a model's rule set (manual edit). Keeps the existing status."""
    record = db.get(ExtractionModelRecord, model_id)
    if record is None:
        return None
    new_json = dict(model_json)
    new_json["status"] = record.status
    record.model_json = new_json
    if "confidence" in model_json:
        record.confidence = model_json["confidence"]
    if updated_by:
        record.updated_by = updated_by
        new_json["updated_by"] = updated_by
    db.commit()
    db.refresh(record)
    return record


def update_model_status(
    db: Session,
    model_id: str,
    status: str,
    approved_by: str | None = None,
    retired_by: str | None = None,
) -> ExtractionModelRecord | None:
    record = db.get(ExtractionModelRecord, model_id)
    if record is None:
        return None
    record.status = status
    model_json = dict(record.model_json or {})
    model_json["status"] = status
    if approved_by:
        approved_at = datetime.now()
        record.approved_by = approved_by
        record.approved_at = approved_at
        model_json["approved_by"] = approved_by
        model_json["approved_at"] = approved_at.isoformat()
    if status == "retired" and retired_by:
        retired_at = datetime.now()
        record.retired_by = retired_by
        record.retired_at = retired_at
        model_json["retired_by"] = retired_by
        model_json["retired_at"] = retired_at.isoformat()
    record.model_json = model_json
    db.commit()
    db.refresh(record)
    return record


def record_model_validation_success(db: Session, model_id: str, intake_id: str) -> ExtractionModelRecord | None:
    record = db.get(ExtractionModelRecord, model_id)
    if record is None:
        return None

    intake_ids = list(record.validation_intake_ids or [])
    record.validation_attempt_count += 1
    if intake_id not in intake_ids:
        intake_ids.append(intake_id)
        record.validation_success_count += 1
    record.validation_intake_ids = intake_ids
    record.approved_after_intake_count = record.validation_success_count
    record.last_validation_error = None
    record.success_rate = (
        record.validation_success_count / record.validation_attempt_count
        if record.validation_attempt_count else 0
    )

    model_json = dict(record.model_json or {})
    model_json["validation_attempt_count"] = record.validation_attempt_count
    model_json["validation_success_count"] = record.validation_success_count
    model_json["validation_failure_count"] = record.validation_failure_count
    model_json["approved_after_intake_count"] = record.approved_after_intake_count
    model_json["last_validation_error"] = None
    model_json["validation_intake_ids"] = intake_ids
    model_json["success_rate"] = record.success_rate
    record.model_json = model_json
    db.commit()
    db.refresh(record)
    return record


def record_model_validation_failure(db: Session, model_id: str, error: str) -> ExtractionModelRecord | None:
    record = db.get(ExtractionModelRecord, model_id)
    if record is None:
        return None

    record.validation_attempt_count += 1
    record.validation_failure_count += 1
    record.last_validation_error = error
    record.success_rate = (
        record.validation_success_count / record.validation_attempt_count
        if record.validation_attempt_count else 0
    )

    model_json = dict(record.model_json or {})
    model_json["validation_attempt_count"] = record.validation_attempt_count
    model_json["validation_success_count"] = record.validation_success_count
    model_json["validation_failure_count"] = record.validation_failure_count
    model_json["approved_after_intake_count"] = record.approved_after_intake_count
    model_json["last_validation_error"] = error
    model_json["validation_intake_ids"] = list(record.validation_intake_ids or [])
    model_json["success_rate"] = record.success_rate
    record.model_json = model_json
    db.commit()
    db.refresh(record)
    return record


def set_model_last_validation_error(db: Session, model_id: str, error: str | None) -> ExtractionModelRecord | None:
    """Set the visible model validation error without changing validation counters.

    Workbench authoring failures are editing feedback, not production validation
    attempts. They need to be visible on the model but should not pollute the
    usage/validation stats cards.
    """
    record = db.get(ExtractionModelRecord, model_id)
    if record is None:
        return None
    record.last_validation_error = error
    model_json = dict(record.model_json or {})
    model_json["last_validation_error"] = error
    model_json["validation_attempt_count"] = record.validation_attempt_count
    model_json["validation_success_count"] = record.validation_success_count
    model_json["validation_failure_count"] = record.validation_failure_count
    model_json["approved_after_intake_count"] = record.approved_after_intake_count
    model_json["validation_intake_ids"] = list(record.validation_intake_ids or [])
    model_json["success_rate"] = record.success_rate
    record.model_json = model_json
    db.commit()
    db.refresh(record)
    return record


# --- ProcessingLog ---


def log_processing_step(db: Session, *, intake_id: str, step: str, status: str,
                        job_id: str | None = None, message: str | None = None,
                        details: dict | None = None,
                        duration_ms: int | None = None) -> ProcessingLog:
    merged_details = dict(details) if details else None
    if duration_ms is not None:
        merged_details = merged_details or {}
        merged_details["duration_ms"] = duration_ms
    log = ProcessingLog(
        intake_id=intake_id,
        job_id=job_id,
        step=step,
        status=status,
        message=message,
        details=merged_details,
        duration_ms=duration_ms,
    )
    db.add(log)
    db.commit()
    return log


def get_processing_logs(db: Session, intake_id: str) -> list[ProcessingLog]:
    stmt = (
        select(ProcessingLog)
        .where(ProcessingLog.intake_id == intake_id)
        .order_by(ProcessingLog.timestamp)
    )
    return list(db.scalars(stmt).all())


# --- Paginated queries ---


def list_intakes(
    db: Session,
    *,
    page: int = 1,
    per_page: int = 25,
    status: str | None = None,
    supplier: str | None = None,
    suppliers: list[str] | None = None,
    search: str | None = None,
    output_type: str | None = None,
    extraction_path: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    intake_source: str | None = None,
    fingerprint: str | None = None,
    include_training: bool = False,
) -> tuple[list[IntakeRecord], int, dict[str, int], dict[str, ExtractionJob]]:
    """List intakes with filters, plus status counts and each intake's latest job.

    Job-based filters (supplier, output_type, extraction_path) match *any* job of
    an intake, while the returned job per intake is the *latest* one. These can
    disagree only for extraction_path on fallback intakes (model -> model_fallback_ai),
    which is acceptable for list display.

    Workbench training samples (`intake_source="training"`) are EXCLUDED by
    default — they are not production invoices and must not show in the Invoices
    list. Pass `include_training=True` (or an explicit `intake_source`) to see them.

    Returns (page_items, total, status_counts, latest_job_by_intake_id).
    status_counts honors every filter except `status`, so UI chips show what
    each status selection would return.
    """
    # Filters shared by the list query and the status-count query.
    base_filters = []

    if not include_training and intake_source != "training":
        base_filters.append(
            (IntakeRecord.intake_source.is_(None)) | (IntakeRecord.intake_source != "training")
        )

    if suppliers:
        sub = select(ExtractionJob.intake_id).where(
            ExtractionJob.supplier_name.in_(suppliers)
        )
        base_filters.append(IntakeRecord.id.in_(sub))
    elif supplier:
        sub = select(ExtractionJob.intake_id).where(
            ExtractionJob.supplier_name.ilike(f"%{supplier}%")
        )
        base_filters.append(IntakeRecord.id.in_(sub))

    if output_type:
        sub = select(ExtractionJob.intake_id).where(
            ExtractionJob.output_type == output_type
        )
        base_filters.append(IntakeRecord.id.in_(sub))

    if extraction_path:
        sub = select(ExtractionJob.intake_id).where(
            ExtractionJob.extraction_path == extraction_path
        )
        base_filters.append(IntakeRecord.id.in_(sub))

    if search:
        base_filters.append(IntakeRecord.original_filename.ilike(f"%{search}%"))

    if date_from:
        base_filters.append(IntakeRecord.created_at >= date_from)

    if date_to:
        base_filters.append(IntakeRecord.created_at < date_to)

    if intake_source:
        base_filters.append(IntakeRecord.intake_source == intake_source)

    if fingerprint:
        base_filters.append(IntakeRecord.layout_fingerprint == fingerprint)

    stmt = select(IntakeRecord).where(*base_filters)
    if status:
        stmt = stmt.where(IntakeRecord.status == status)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.scalar(count_stmt) or 0

    stmt = stmt.order_by(IntakeRecord.created_at.desc())
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)
    items = list(db.scalars(stmt).all())

    counts_stmt = (
        select(IntakeRecord.status, func.count())
        .where(*base_filters)
        .group_by(IntakeRecord.status)
    )
    status_counts = {row[0]: row[1] for row in db.execute(counts_stmt).all()}

    # Batch-fetch jobs for the page and keep the latest per intake.
    # created_at is second-granular on MySQL, so break ties by id.
    latest_jobs: dict[str, ExtractionJob] = {}
    intake_ids = [i.id for i in items]
    if intake_ids:
        jobs_stmt = (
            select(ExtractionJob)
            .where(ExtractionJob.intake_id.in_(intake_ids))
            .order_by(ExtractionJob.created_at.desc(), ExtractionJob.id.desc())
        )
        for job in db.scalars(jobs_stmt).all():
            latest_jobs.setdefault(job.intake_id, job)

    return items, total, status_counts, latest_jobs


def list_distinct_supplier_names(db: Session) -> list[str]:
    """Distinct supplier names from extraction jobs for list filter facets."""
    stmt = (
        select(ExtractionJob.supplier_name)
        .where(
            ExtractionJob.supplier_name.isnot(None),
            ExtractionJob.supplier_name != "",
        )
        .distinct()
        .order_by(ExtractionJob.supplier_name)
    )
    return [name for name in db.scalars(stmt).all() if name]


def purge_all_intakes(db: Session) -> int:
    """Delete all intake records and related rows. Returns deleted intake count."""
    from sqlalchemy import delete

    intake_ids = list(db.scalars(select(IntakeRecord.id)).all())
    if not intake_ids:
        return 0

    db.execute(delete(ProcessingLog).where(ProcessingLog.intake_id.in_(intake_ids)))
    db.execute(delete(ExtractionJob).where(ExtractionJob.intake_id.in_(intake_ids)))
    db.execute(delete(AICostLog).where(AICostLog.intake_id.in_(intake_ids)))
    db.execute(delete(IntakeRecord).where(IntakeRecord.id.in_(intake_ids)))
    db.commit()
    return len(intake_ids)


def list_extraction_models(
    db: Session,
    *,
    page: int = 1,
    per_page: int = 25,
    status: str | None = None,
    supplier: str | None = None,
) -> tuple[list[ExtractionModelRecord], int]:
    stmt = select(ExtractionModelRecord)

    if status:
        stmt = stmt.where(ExtractionModelRecord.status == status)
    if supplier:
        stmt = stmt.where(ExtractionModelRecord.supplier.ilike(f"%{supplier}%"))

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.scalar(count_stmt) or 0

    stmt = stmt.order_by(ExtractionModelRecord.created_at.desc())
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    items = list(db.scalars(stmt).all())
    return items, total


def list_extraction_jobs(
    db: Session,
    *,
    page: int = 1,
    per_page: int = 25,
    status: str | None = None,
    extraction_path: str | None = None,
) -> tuple[list[ExtractionJob], int]:
    stmt = select(ExtractionJob)

    if status:
        stmt = stmt.where(ExtractionJob.status == status)
    if extraction_path:
        stmt = stmt.where(ExtractionJob.extraction_path == extraction_path)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.scalar(count_stmt) or 0

    stmt = stmt.order_by(ExtractionJob.created_at.desc())
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    items = list(db.scalars(stmt).all())
    return items, total


def list_processing_logs(
    db: Session,
    *,
    page: int = 1,
    per_page: int = 25,
    intake_id: str | None = None,
    step: str | None = None,
    status: str | None = None,
) -> tuple[list[ProcessingLog], int]:
    stmt = select(ProcessingLog)

    if intake_id:
        stmt = stmt.where(ProcessingLog.intake_id == intake_id)
    if step:
        stmt = stmt.where(ProcessingLog.step == step)
    if status:
        stmt = stmt.where(ProcessingLog.status == status)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.scalar(count_stmt) or 0

    stmt = stmt.order_by(ProcessingLog.timestamp.desc())
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    items = list(db.scalars(stmt).all())
    return items, total


# --- AI Cost Logging ---


def log_ai_call(
    db: Session,
    *,
    intake_id: str | None = None,
    job_id: str | None = None,
    call_type: str,
    ai_model_name: str,
    tokens_input: int = 0,
    tokens_output: int = 0,
    tokens_cached: int = 0,
    estimated_cost_usd: float = 0,
    duration_ms: int = 0,
    status: str = "success",
    error_message: str | None = None,
) -> AICostLog | None:
    """Persist a per-call AI cost row.

    Cost logging is non-critical bookkeeping, so a failure here must never abort
    the extraction/authoring work that triggered it. On a DB error we roll back
    (recovering the session for the caller's subsequent work) and return None
    instead of letting the exception poison the transaction.
    """
    record = AICostLog(
        intake_id=intake_id,
        job_id=job_id,
        call_type=call_type,
        ai_model_name=ai_model_name,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        tokens_cached=tokens_cached,
        estimated_cost_usd=estimated_cost_usd,
        duration_ms=duration_ms,
        status=status,
        error_message=error_message,
    )
    try:
        db.add(record)
        db.commit()
        db.refresh(record)
        return record
    except SQLAlchemyError as exc:
        db.rollback()
        logger.warning(
            "ai_cost_log.persist_failed",
            intake_id=intake_id,
            job_id=job_id,
            call_type=call_type,
            error=str(exc),
        )
        return None


def get_ai_cost_logs(
    db: Session,
    *,
    intake_id: str | None = None,
    call_type: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> tuple[list[AICostLog], int]:
    stmt = select(AICostLog)
    if intake_id:
        stmt = stmt.where(AICostLog.intake_id == intake_id)
    if call_type:
        stmt = stmt.where(AICostLog.call_type == call_type)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.scalar(count_stmt) or 0

    stmt = stmt.order_by(AICostLog.created_at.desc())
    stmt = stmt.offset((page - 1) * per_page).limit(per_page)

    items = list(db.scalars(stmt).all())
    return items, total


def get_ai_cost_summary(db: Session, days: int = 30) -> dict:
    """Per-call-type cost breakdown over the last N days."""
    since = datetime.now() - timedelta(days=days)
    stmt = (
        select(
            AICostLog.call_type,
            func.count().label("call_count"),
            func.coalesce(func.sum(AICostLog.estimated_cost_usd), 0).label("total_cost"),
            func.coalesce(func.sum(AICostLog.tokens_input), 0).label("total_tokens_input"),
            func.coalesce(func.sum(AICostLog.tokens_output), 0).label("total_tokens_output"),
            func.coalesce(func.sum(AICostLog.tokens_cached), 0).label("total_tokens_cached"),
        )
        .where(AICostLog.created_at >= since)
        .group_by(AICostLog.call_type)
    )
    rows = db.execute(stmt).all()
    return {
        r.call_type: {
            "call_count": r.call_count,
            "total_cost": float(r.total_cost),
            "total_tokens_input": int(r.total_tokens_input),
            "total_tokens_output": int(r.total_tokens_output),
            "total_tokens_cached": int(r.total_tokens_cached),
        }
        for r in rows
    }


# --- Dashboard aggregations ---


def get_ai_cost_breakdown(db: Session, days: int = 30) -> dict:
    """AI cost split by purpose: production extraction vs classification vs model
    training. Joins each cost row to its intake's source so training-sample
    extraction (intake_source="training") is attributed to model training, not
    production. Returns per-bucket cost+tokens and a grand total (everything)."""
    since = datetime.now() - timedelta(days=days)
    rows = db.execute(
        select(
            AICostLog.call_type,
            IntakeRecord.intake_source,
            func.coalesce(func.sum(AICostLog.estimated_cost_usd), 0).label("cost"),
            func.coalesce(func.sum(AICostLog.tokens_input + AICostLog.tokens_output), 0).label("tokens"),
            func.count().label("calls"),
        )
        .select_from(AICostLog)
        .join(IntakeRecord, AICostLog.intake_id == IntakeRecord.id, isouter=True)
        .where(AICostLog.created_at >= since)
        .group_by(AICostLog.call_type, IntakeRecord.intake_source)
    ).all()

    buckets = {
        "production_extraction": {"cost": 0.0, "tokens": 0, "calls": 0},
        "classification": {"cost": 0.0, "tokens": 0, "calls": 0},
        "model_training": {"cost": 0.0, "tokens": 0, "calls": 0},
    }
    for r in rows:
        is_training = r.intake_source == "training"
        if r.call_type == "model_authoring" or (r.call_type == "extraction" and is_training):
            key = "model_training"
        elif r.call_type == "classification":
            key = "classification"
        elif r.call_type == "extraction":
            key = "production_extraction"
        else:
            key = "model_training" if is_training else "production_extraction"
        buckets[key]["cost"] += float(r.cost)
        buckets[key]["tokens"] += int(r.tokens)
        buckets[key]["calls"] += int(r.calls)

    total_cost = round(sum(b["cost"] for b in buckets.values()), 6)
    for b in buckets.values():
        b["cost"] = round(b["cost"], 6)
    return {"days": days, "total_cost": total_cost, "buckets": buckets}


def get_dashboard_stats(db: Session) -> dict:
    # Production invoices only — workbench training samples are not invoices.
    prod = (IntakeRecord.intake_source.is_(None)) | (IntakeRecord.intake_source != "training")
    total = db.scalar(select(func.count()).select_from(IntakeRecord).where(prod)) or 0
    # "completed_with_warnings" was still delivered — count it as completed.
    completed = db.scalar(
        select(func.count()).select_from(IntakeRecord)
        .where(prod, IntakeRecord.status.in_(["completed", "completed_with_warnings"]))
    ) or 0
    completed_with_warnings = db.scalar(
        select(func.count()).select_from(IntakeRecord)
        .where(prod, IntakeRecord.status == "completed_with_warnings")
    ) or 0
    failed = db.scalar(
        select(func.count()).select_from(IntakeRecord).where(prod, IntakeRecord.status == "failed")
    ) or 0

    total_models = db.scalar(select(func.count()).select_from(ExtractionModelRecord)) or 0
    approved_models = db.scalar(
        select(func.count()).select_from(ExtractionModelRecord)
        .where(ExtractionModelRecord.status == "approved")
    ) or 0

    total_ai_cost = db.scalar(
        select(func.coalesce(func.sum(AICostLog.estimated_cost_usd), 0))
    ) or 0
    # All-time AI cost spent building/training models: authoring + the AI
    # extraction of training samples (intake_source="training").
    training_cost = db.scalar(
        select(func.coalesce(func.sum(AICostLog.estimated_cost_usd), 0))
        .select_from(AICostLog)
        .join(IntakeRecord, AICostLog.intake_id == IntakeRecord.id, isouter=True)
        .where(
            (AICostLog.call_type == "model_authoring")
            | ((AICostLog.call_type == "extraction") & (IntakeRecord.intake_source == "training"))
        )
    ) or 0

    model_path_count = db.scalar(
        select(func.count()).select_from(ExtractionJob)
        .where(ExtractionJob.extraction_path == "model")
    ) or 0
    ai_path_count = db.scalar(
        select(func.count()).select_from(ExtractionJob)
        .where(ExtractionJob.extraction_path == "ai")
    ) or 0

    # Everything an operator still has to look at: hard failures PLUS invoices
    # delivered with warnings (failed reconciliation, delivery blocked, extraction
    # exceptions). Previously this repeated the `failed` query verbatim, so the
    # dashboard under-reported the real review queue.
    pending_exceptions = db.scalar(
        select(func.count()).select_from(IntakeRecord)
        .where(prod, IntakeRecord.status.in_(["failed", "completed_with_warnings"]))
    ) or 0

    # Deliverable accuracy: jobs whose deterministic line-items-vs-total check
    # passed. The old "success_rate" counted `completed_with_warnings` as a
    # success, which is exactly where reconciliation FAILURES land — so it
    # reported ~90% while barely half of invoices actually reconciled.
    reconciled_jobs = db.scalar(
        select(func.count()).select_from(ExtractionJob)
        .where(ExtractionJob.is_reconciled.is_(True))
    ) or 0
    scored_jobs = db.scalar(
        select(func.count()).select_from(ExtractionJob)
        .where(ExtractionJob.is_reconciled.isnot(None))
    ) or 0

    return {
        "total_invoices": total,
        "completed_invoices": completed,
        "completed_with_warnings": completed_with_warnings,
        "failed_invoices": failed,
        "pending_exceptions": pending_exceptions,
        # Share of invoices that finished the pipeline at all.
        "completion_rate": (completed / total * 100) if total > 0 else 0,
        # Share of extracted invoices whose totals actually reconcile. This is
        # the accuracy number; keep them distinct.
        "success_rate": (reconciled_jobs / scored_jobs * 100) if scored_jobs > 0 else 0,
        "reconciled_jobs": reconciled_jobs,
        "scored_jobs": scored_jobs,
        "total_ai_cost": float(total_ai_cost),
        "model_training_cost": float(training_cost),
        "extraction_cost": float(total_ai_cost) - float(training_cost),
        "total_models": total_models,
        "approved_models": approved_models,
        "model_path_count": model_path_count,
        "ai_path_count": ai_path_count,
    }


def get_volume_over_time(db: Session, days: int = 30) -> list[dict]:
    since = datetime.now() - timedelta(days=days)
    prod = (IntakeRecord.intake_source.is_(None)) | (IntakeRecord.intake_source != "training")
    stmt = (
        select(
            func.date(IntakeRecord.created_at).label("date"),
            func.count().label("count"),
        )
        .where(prod, IntakeRecord.created_at >= since)
        .group_by(func.date(IntakeRecord.created_at))
        .order_by(func.date(IntakeRecord.created_at))
    )
    rows = db.execute(stmt).all()
    return [{"date": str(r.date), "count": r.count} for r in rows]


def get_cost_over_time(db: Session, days: int = 30) -> list[dict]:
    since = datetime.now() - timedelta(days=days)
    stmt = (
        select(
            func.date(AICostLog.created_at).label("date"),
            func.coalesce(func.sum(AICostLog.estimated_cost_usd), 0).label("cost"),
            func.coalesce(func.sum(AICostLog.tokens_input), 0).label("tokens_input"),
            func.coalesce(func.sum(AICostLog.tokens_output), 0).label("tokens_output"),
            func.coalesce(func.sum(AICostLog.tokens_cached), 0).label("tokens_cached"),
        )
        .where(AICostLog.created_at >= since)
        .group_by(func.date(AICostLog.created_at))
        .order_by(func.date(AICostLog.created_at))
    )
    rows = db.execute(stmt).all()
    return [
        {"date": str(r.date), "cost": float(r.cost),
         "tokens_input": int(r.tokens_input), "tokens_output": int(r.tokens_output),
         "tokens_cached": int(r.tokens_cached)}
        for r in rows
    ]


# --- Monthly cost aggregations ---


def _month_window(month: str) -> tuple[datetime, datetime]:
    """Parse a ``YYYY-MM`` month into a half-open ``[start, next_month_start)``
    datetime window. Dialect-agnostic: we filter on the window in Python-computed
    bounds rather than a DB-specific month-truncation function."""
    year, mon = (int(part) for part in month.split("-", 1))
    start = datetime(year, mon, 1)
    end = datetime(year + 1, 1, 1) if mon == 12 else datetime(year, mon + 1, 1)
    return start, end


def _recent_month_keys(months: int, *, now: datetime | None = None) -> list[str]:
    """The last ``months`` ``YYYY-MM`` keys, oldest first, ending at the current month."""
    ref = now or datetime.now()
    keys: list[str] = []
    year, mon = ref.year, ref.month
    for _ in range(months):
        keys.append(f"{year:04d}-{mon:02d}")
        mon -= 1
        if mon == 0:
            mon = 12
            year -= 1
    return list(reversed(keys))


def get_monthly_stats(db: Session, month: str) -> dict:
    """Single-month rollup scoped to ``YYYY-MM``: total AI cost split by purpose
    (extraction / classification / model training, using the same intake-source
    attribution as :func:`get_ai_cost_breakdown`), token totals, AI-call count,
    production invoice outcomes, and the AI-vs-$0 extraction-path split."""
    start, end = _month_window(month)

    # Cost by purpose (join each cost row to its intake's source).
    cost_rows = db.execute(
        select(
            AICostLog.call_type,
            IntakeRecord.intake_source,
            func.coalesce(func.sum(AICostLog.estimated_cost_usd), 0).label("cost"),
            func.coalesce(func.sum(AICostLog.tokens_input), 0).label("tokens_input"),
            func.coalesce(func.sum(AICostLog.tokens_output), 0).label("tokens_output"),
            func.count().label("calls"),
        )
        .select_from(AICostLog)
        .join(IntakeRecord, AICostLog.intake_id == IntakeRecord.id, isouter=True)
        .where(AICostLog.created_at >= start, AICostLog.created_at < end)
        .group_by(AICostLog.call_type, IntakeRecord.intake_source)
    ).all()

    extraction_cost = classification_cost = training_cost = 0.0
    tokens_input = tokens_output = ai_calls = 0
    for r in cost_rows:
        is_training = r.intake_source == "training"
        if r.call_type == "model_authoring" or (r.call_type == "extraction" and is_training):
            training_cost += float(r.cost)
        elif r.call_type == "classification":
            classification_cost += float(r.cost)
        elif r.call_type == "extraction":
            extraction_cost += float(r.cost)
        else:
            training_cost += float(r.cost) if is_training else 0.0
            extraction_cost += 0.0 if is_training else float(r.cost)
        tokens_input += int(r.tokens_input)
        tokens_output += int(r.tokens_output)
        ai_calls += int(r.calls)

    total_cost = extraction_cost + classification_cost + training_cost

    # Production invoice outcomes for the month.
    prod = (IntakeRecord.intake_source.is_(None)) | (IntakeRecord.intake_source != "training")
    month_prod = (prod, IntakeRecord.created_at >= start, IntakeRecord.created_at < end)
    invoices_processed = db.scalar(select(func.count()).select_from(IntakeRecord).where(*month_prod)) or 0
    invoices_completed = db.scalar(
        select(func.count()).select_from(IntakeRecord)
        .where(*month_prod, IntakeRecord.status.in_(["completed", "completed_with_warnings"]))
    ) or 0
    invoices_failed = db.scalar(
        select(func.count()).select_from(IntakeRecord).where(*month_prod, IntakeRecord.status == "failed")
    ) or 0

    # AI-vs-$0 extraction-path split for the month.
    job_window = (ExtractionJob.created_at >= start, ExtractionJob.created_at < end)
    ai_path_count = db.scalar(
        select(func.count()).select_from(ExtractionJob).where(*job_window, ExtractionJob.extraction_path == "ai")
    ) or 0
    model_path_count = db.scalar(
        select(func.count()).select_from(ExtractionJob).where(*job_window, ExtractionJob.extraction_path == "model")
    ) or 0

    return {
        "month": month,
        "total_cost": round(total_cost, 6),
        "extraction_cost": round(extraction_cost, 6),
        "classification_cost": round(classification_cost, 6),
        "training_cost": round(training_cost, 6),
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "ai_calls": ai_calls,
        "invoices_processed": invoices_processed,
        "invoices_completed": invoices_completed,
        "invoices_failed": invoices_failed,
        "ai_path_count": ai_path_count,
        "model_path_count": model_path_count,
    }


def get_cost_by_month(db: Session, months: int = 12) -> list[dict]:
    """AI cost per calendar month for the last ``months`` months, oldest first.
    Missing months are filled with ``cost: 0`` so the trend chart is continuous.
    Grouped with portable ``func.extract`` (works on MySQL and SQLite)."""
    keys = _recent_month_keys(months)
    start, _ = _month_window(keys[0])
    rows = db.execute(
        select(
            extract("year", AICostLog.created_at).label("y"),
            extract("month", AICostLog.created_at).label("m"),
            func.coalesce(func.sum(AICostLog.estimated_cost_usd), 0).label("cost"),
        )
        .where(AICostLog.created_at >= start)
        .group_by(extract("year", AICostLog.created_at), extract("month", AICostLog.created_at))
    ).all()
    by_key = {f"{int(r.y):04d}-{int(r.m):02d}": float(r.cost) for r in rows}
    return [{"month": key, "cost": round(by_key.get(key, 0.0), 6)} for key in keys]


def get_cost_by_model(db: Session, month: str) -> list[dict]:
    """Per-AI-model cost for a month, highest cost first."""
    start, end = _month_window(month)
    rows = db.execute(
        select(
            AICostLog.ai_model_name,
            func.coalesce(func.sum(AICostLog.estimated_cost_usd), 0).label("cost"),
            func.count().label("calls"),
            func.coalesce(func.sum(AICostLog.tokens_input + AICostLog.tokens_output), 0).label("tokens"),
        )
        .where(AICostLog.created_at >= start, AICostLog.created_at < end)
        .group_by(AICostLog.ai_model_name)
    ).all()
    result = [
        {"model": r.ai_model_name or "unknown", "cost": round(float(r.cost), 6),
         "calls": int(r.calls), "tokens": int(r.tokens)}
        for r in rows
    ]
    result.sort(key=lambda x: x["cost"], reverse=True)
    return result


def get_cost_by_supplier(db: Session, month: str) -> list[dict]:
    """Per-supplier/account cost for a month, highest cost first. Cost rows whose
    intake doesn't join (synthetic ``authoring-*`` ids) surface with a null
    ``supplier_profile_id`` so the UI can label them as unattributed."""
    start, end = _month_window(month)
    rows = db.execute(
        select(
            IntakeRecord.supplier_profile_id,
            IntakeRecord.account_label,
            func.coalesce(func.sum(AICostLog.estimated_cost_usd), 0).label("cost"),
            func.count().label("calls"),
        )
        .select_from(AICostLog)
        .join(IntakeRecord, AICostLog.intake_id == IntakeRecord.id, isouter=True)
        .where(AICostLog.created_at >= start, AICostLog.created_at < end)
        .group_by(IntakeRecord.supplier_profile_id, IntakeRecord.account_label)
    ).all()
    result = [
        {"supplier_profile_id": r.supplier_profile_id, "account_label": r.account_label,
         "cost": round(float(r.cost), 6), "calls": int(r.calls)}
        for r in rows
    ]
    result.sort(key=lambda x: x["cost"], reverse=True)
    return result


def delete_intake(db: Session, intake_id: str) -> bool:
    record = db.get(IntakeRecord, intake_id)
    if record is None:
        return False
    db.execute(delete(ProcessingLog).where(ProcessingLog.intake_id == intake_id))
    db.execute(delete(ExtractionJob).where(ExtractionJob.intake_id == intake_id))
    db.execute(delete(AICostLog).where(AICostLog.intake_id == intake_id))
    db.delete(record)
    db.commit()
    return True


def delete_extraction_model(db: Session, model_id: str) -> bool:
    record = db.get(ExtractionModelRecord, model_id)
    if record is None:
        return False
    db.execute(delete(ModelTrainingRun).where(ModelTrainingRun.model_id == model_id))
    db.delete(record)
    db.commit()
    return True


# --- Model training runs (workbench training pipeline) ---


def create_training_run(db: Session, *, model_id: str, supplier_profile_id: str | None,
                        total_steps: int, message: str | None = None) -> ModelTrainingRun:
    run = ModelTrainingRun(
        model_id=model_id, supplier_profile_id=supplier_profile_id,
        state="running", step="extracting", total_steps=total_steps,
        completed_steps=0, message=message, detail={},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def update_training_run(db: Session, run_id: str, **kwargs) -> ModelTrainingRun | None:
    run = db.get(ModelTrainingRun, run_id)
    if run is None:
        return None
    for key, value in kwargs.items():
        if hasattr(run, key):
            setattr(run, key, value)
    db.commit()
    db.refresh(run)
    return run


def get_latest_training_run(db: Session, model_id: str) -> ModelTrainingRun | None:
    stmt = (
        select(ModelTrainingRun)
        .where(ModelTrainingRun.model_id == model_id)
        .order_by(ModelTrainingRun.started_at.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def fail_stale_training_runs(db: Session, message: str = "Worker restarted") -> int:
    """Mark every still-'running' run as failed (its task is dead).

    Called on worker startup: a restart kills any in-flight Celery task, so a
    run left 'running' is orphaned — finalize it so the workbench stops polling.
    Returns the number of runs swept.
    """
    runs = list(db.scalars(
        select(ModelTrainingRun).where(ModelTrainingRun.state == "running")
    ).all())
    if not runs:
        return 0
    for run in runs:
        run.state = "failed"
        run.step = "done"
        run.message = message
        run.error_message = message
        run.finished_at = datetime.now()
    db.commit()
    return len(runs)


# ── Profile authoring sessions ───────────────────────────────────────────────

def create_authoring_session(
    db: Session, *, supplier_name: str | None, output_spec_id: str, field_schema_id: str,
    source_profile_id: str | None = None, draft_profile: dict | None = None,
    category_override: str | None = None,
) -> ProfileAuthoringSession:
    session = ProfileAuthoringSession(
        supplier_name=supplier_name,
        output_spec_id=output_spec_id,
        field_schema_id=field_schema_id,
        source_profile_id=source_profile_id,
        draft_profile=draft_profile,
        conversation=[],
        category_override=category_override,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_authoring_session(db: Session, session_id: str) -> ProfileAuthoringSession | None:
    return db.get(ProfileAuthoringSession, session_id)


def update_authoring_session(db: Session, session_id: str, **kwargs) -> ProfileAuthoringSession | None:
    session = db.get(ProfileAuthoringSession, session_id)
    if session is None:
        return None
    for key, value in kwargs.items():
        if hasattr(session, key):
            setattr(session, key, value)
    db.commit()
    db.refresh(session)
    return session


def add_authoring_invoice(
    db: Session, *, session_id: str, filename: str, has_expected: bool, artifact_dir: str,
) -> ProfileAuthoringInvoice:
    invoice = ProfileAuthoringInvoice(
        session_id=session_id, filename=filename, has_expected=has_expected,
        # Samples are extracted lazily on the first authoring turn, not on upload.
        artifact_dir=artifact_dir, extraction_status="uploaded",
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return invoice


def get_authoring_invoice(db: Session, invoice_id: str) -> ProfileAuthoringInvoice | None:
    return db.get(ProfileAuthoringInvoice, invoice_id)


def list_authoring_invoices(db: Session, session_id: str) -> list[ProfileAuthoringInvoice]:
    stmt = (
        select(ProfileAuthoringInvoice)
        .where(ProfileAuthoringInvoice.session_id == session_id)
        .order_by(ProfileAuthoringInvoice.created_at.asc())
    )
    return list(db.scalars(stmt).all())


def update_authoring_invoice(db: Session, invoice_id: str, **kwargs) -> ProfileAuthoringInvoice | None:
    invoice = db.get(ProfileAuthoringInvoice, invoice_id)
    if invoice is None:
        return None
    for key, value in kwargs.items():
        if hasattr(invoice, key):
            setattr(invoice, key, value)
    db.commit()
    db.refresh(invoice)
    return invoice


def delete_authoring_invoice(db: Session, invoice_id: str) -> bool:
    invoice = db.get(ProfileAuthoringInvoice, invoice_id)
    if invoice is None:
        return False
    db.delete(invoice)
    db.commit()
    return True


def add_authoring_blueprint(
    db: Session,
    *,
    session_id: str,
    filename: str,
    artifact_path: str,
    invoice_id: str | None = None,
) -> ProfileAuthoringBlueprint:
    blueprint = ProfileAuthoringBlueprint(
        session_id=session_id,
        invoice_id=invoice_id,
        filename=filename,
        artifact_path=artifact_path,
    )
    db.add(blueprint)
    db.commit()
    db.refresh(blueprint)
    return blueprint


def list_authoring_blueprints(db: Session, session_id: str) -> list[ProfileAuthoringBlueprint]:
    stmt = (
        select(ProfileAuthoringBlueprint)
        .where(ProfileAuthoringBlueprint.session_id == session_id)
        .order_by(ProfileAuthoringBlueprint.created_at.asc())
    )
    return list(db.scalars(stmt).all())


def get_authoring_blueprint(db: Session, blueprint_id: str) -> ProfileAuthoringBlueprint | None:
    return db.get(ProfileAuthoringBlueprint, blueprint_id)


def update_authoring_blueprint(
    db: Session, blueprint_id: str, **kwargs,
) -> ProfileAuthoringBlueprint | None:
    blueprint = db.get(ProfileAuthoringBlueprint, blueprint_id)
    if blueprint is None:
        return None
    for key, value in kwargs.items():
        if hasattr(blueprint, key):
            setattr(blueprint, key, value)
    db.commit()
    db.refresh(blueprint)
    return blueprint


def delete_authoring_blueprint(db: Session, blueprint_id: str) -> bool:
    blueprint = db.get(ProfileAuthoringBlueprint, blueprint_id)
    if blueprint is None:
        return False
    db.delete(blueprint)
    db.commit()
    return True


def create_authoring_job(
    db: Session,
    *,
    session_id: str,
    kind: str,
    message: str | None = None,
    invoice_id: str | None = None,
) -> ProfileAuthoringJob:
    latest_created_at = db.scalar(
        select(func.max(ProfileAuthoringJob.created_at)).where(
            ProfileAuthoringJob.session_id == session_id
        )
    )
    created_at = datetime.now()
    if latest_created_at is not None and created_at <= latest_created_at:
        created_at = latest_created_at + timedelta(microseconds=1)
    job = ProfileAuthoringJob(
        id=generate_job_id(),
        session_id=session_id,
        kind=kind,
        status="queued",
        message=message,
        invoice_id=invoice_id,
        created_at=created_at,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def generate_job_id() -> str:
    from uuid import uuid4

    return uuid4().hex


def get_authoring_job(db: Session, job_id: str) -> ProfileAuthoringJob | None:
    return db.get(ProfileAuthoringJob, job_id)


def list_authoring_jobs(
    db: Session,
    session_id: str,
    *,
    statuses: set[str] | None = None,
    limit: int | None = None,
) -> list[ProfileAuthoringJob]:
    stmt = (
        select(ProfileAuthoringJob)
        .where(ProfileAuthoringJob.session_id == session_id)
        .order_by(ProfileAuthoringJob.created_at.asc(), ProfileAuthoringJob.id.asc())
    )
    if statuses:
        stmt = stmt.where(ProfileAuthoringJob.status.in_(statuses))
    if limit:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def list_recent_authoring_jobs(db: Session, session_id: str, limit: int = 25) -> list[ProfileAuthoringJob]:
    stmt = (
        select(ProfileAuthoringJob)
        .where(ProfileAuthoringJob.session_id == session_id)
        .order_by(ProfileAuthoringJob.created_at.desc(), ProfileAuthoringJob.id.desc())
        .limit(limit)
    )
    jobs = list(db.scalars(stmt).all())
    return list(reversed(jobs))


def get_next_queued_authoring_job(db: Session, session_id: str) -> ProfileAuthoringJob | None:
    stmt = (
        select(ProfileAuthoringJob)
        .where(
            ProfileAuthoringJob.session_id == session_id,
            ProfileAuthoringJob.status == "queued",
        )
        .order_by(ProfileAuthoringJob.created_at.asc(), ProfileAuthoringJob.id.asc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def update_authoring_job(db: Session, job_id: str, **kwargs) -> ProfileAuthoringJob | None:
    job = db.get(ProfileAuthoringJob, job_id)
    if job is None:
        return None
    for key, value in kwargs.items():
        if hasattr(job, key):
            setattr(job, key, value)
    db.commit()
    db.refresh(job)
    return job


def cancel_active_authoring_jobs(db: Session, session_id: str) -> int:
    """Cancel a session's queued/running work and make it usable again.

    Celery revocation is handled by the API before this database transition. Any
    sample interrupted during extraction is returned to ``uploaded`` so a later
    turn can safely restart it instead of leaving the UI polling ``pending``.
    """
    now = datetime.now()
    result = db.execute(
        update(ProfileAuthoringJob)
        .where(
            ProfileAuthoringJob.session_id == session_id,
            ProfileAuthoringJob.status.in_(["queued", "running"]),
        )
        .values(
            status="cancelled",
            error_message="Cancelled by user",
            finished_at=now,
            updated_at=now,
        )
    )
    db.execute(
        update(ProfileAuthoringInvoice)
        .where(
            ProfileAuthoringInvoice.session_id == session_id,
            ProfileAuthoringInvoice.extraction_status == "pending",
        )
        .values(extraction_status="uploaded", error_message=None, updated_at=now)
    )
    db.execute(
        update(ProfileAuthoringSession)
        .where(
            ProfileAuthoringSession.id == session_id,
            ProfileAuthoringSession.status == "running",
        )
        .values(status="active", updated_at=now)
    )
    db.commit()
    db.expire_all()
    return int(result.rowcount or 0)


def has_active_authoring_jobs(db: Session, session_id: str) -> bool:
    stmt = select(func.count()).select_from(ProfileAuthoringJob).where(
        ProfileAuthoringJob.session_id == session_id,
        ProfileAuthoringJob.status.in_(["queued", "running"]),
    )
    return bool(db.scalar(stmt) or 0)


def try_start_authoring_queue(db: Session, session_id: str) -> bool:
    stmt = (
        update(ProfileAuthoringSession)
        .where(
            ProfileAuthoringSession.id == session_id,
            ProfileAuthoringSession.status == "active",
        )
        .values(status="running")
    )
    result = db.execute(stmt)
    db.commit()
    return bool(result.rowcount)
