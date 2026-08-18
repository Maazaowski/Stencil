"""Exception queue API endpoints."""

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from stencil.api.deps import DbSession
from stencil.api.schemas import ExceptionResponse, MessageResponse
from stencil.config import settings
from stencil.db import crud

router = APIRouter(prefix="/exceptions", tags=["exceptions"])


def _scan_exception_dirs(db) -> list[dict]:
    """Scan the exceptions directory and enrich with DB data."""
    exceptions_dir = settings.exceptions_dir
    if not exceptions_dir.exists():
        return []

    results = []
    for entry in sorted(exceptions_dir.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        intake_id = entry.name
        error_log_path = entry / "error_log.json"
        if not error_log_path.exists():
            continue

        error_log = json.loads(error_log_path.read_text(encoding="utf-8"))
        intake = crud.get_intake(db, intake_id)

        results.append({
            "intake_id": intake_id,
            "reason": error_log.get("reason", "unknown"),
            "message": error_log.get("message", ""),
            "details": error_log.get("details", {}),
            "timestamp": error_log.get("timestamp", ""),
            "original_filename": intake.original_filename if intake else None,
            "status": intake.status if intake else "unknown",
            "has_partial_extraction": (entry / "partial_extraction.json").exists(),
            "has_original_pdf": (entry / "original.pdf").exists(),
        })
    return results


@router.get("", response_model=list[ExceptionResponse])
def list_exceptions(db: DbSession):
    return _scan_exception_dirs(db)


@router.get("/{intake_id}", response_model=ExceptionResponse)
def get_exception(db: DbSession, intake_id: str):
    exception_dir = settings.exceptions_dir / intake_id
    error_log_path = exception_dir / "error_log.json"

    if not error_log_path.exists():
        raise HTTPException(status_code=404, detail="Exception not found")

    error_log = json.loads(error_log_path.read_text(encoding="utf-8"))
    intake = crud.get_intake(db, intake_id)

    return ExceptionResponse(
        intake_id=intake_id,
        reason=error_log.get("reason", "unknown"),
        message=error_log.get("message", ""),
        details=error_log.get("details", {}),
        timestamp=error_log.get("timestamp", ""),
        original_filename=intake.original_filename if intake else None,
        status=intake.status if intake else "unknown",
        has_partial_extraction=(exception_dir / "partial_extraction.json").exists(),
        has_original_pdf=(exception_dir / "original.pdf").exists(),
    )


@router.get("/{intake_id}/pdf")
def download_exception_pdf(intake_id: str):
    pdf_path = settings.exceptions_dir / intake_id / "original.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Original PDF not found")
    return FileResponse(path=str(pdf_path), filename=f"{intake_id}.pdf")


@router.get("/{intake_id}/partial")
def get_partial_extraction(intake_id: str):
    path = settings.exceptions_dir / intake_id / "partial_extraction.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No partial extraction available")
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/{intake_id}/retry", response_model=MessageResponse)
def retry_exception(db: DbSession, intake_id: str):
    exception_dir = settings.exceptions_dir / intake_id
    if not exception_dir.exists():
        raise HTTPException(status_code=404, detail="Exception not found")

    intake = crud.get_intake(db, intake_id)
    if intake is None:
        raise HTTPException(status_code=404, detail="Intake record not found")

    source_pdf = exception_dir / "original.pdf"
    if not source_pdf.exists() and intake.archive_pdf_path:
        source_pdf = Path(intake.archive_pdf_path)
    if not source_pdf.exists():
        raise HTTPException(status_code=404, detail="Original PDF not found")

    processing_dir = settings.processing_dir / intake_id
    processing_dir.mkdir(parents=True, exist_ok=True)
    retry_path = processing_dir / "original.pdf"
    shutil.copy2(source_pdf, retry_path)

    from stencil.tasks.worker import continue_pipeline_task

    async_result = continue_pipeline_task.delay(intake_id)
    intake.status = "processing"
    intake.error_message = None
    db.commit()
    shutil.rmtree(exception_dir, ignore_errors=True)

    return MessageResponse(
        message=f"Intake {intake_id} queued for retry. Task ID: {async_result.id}",
    )


@router.put("/{intake_id}/resolve", response_model=MessageResponse)
def resolve_exception(db: DbSession, intake_id: str):
    exception_dir = settings.exceptions_dir / intake_id
    if not exception_dir.exists():
        raise HTTPException(status_code=404, detail="Exception not found")

    crud.update_intake_status(db, intake_id, "resolved")
    shutil.rmtree(exception_dir, ignore_errors=True)

    return MessageResponse(
        message=f"Exception for {intake_id} marked as resolved.",
    )
