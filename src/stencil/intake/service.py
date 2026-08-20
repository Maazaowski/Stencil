"""Intake service: registers new PDFs, archives originals, creates DB records."""

import shutil
from pathlib import Path

import structlog
from sqlalchemy.orm import Session

from stencil.config import settings
from stencil.db import crud

logger = structlog.get_logger()


def process_new_pdf(
    db: Session,
    pdf_path: Path,
    *,
    intake_source: str | None = None,
    supplier_profile_id: str | None = None,
    account_label: str | None = None,
    status: str = "processing",
    original_filename: str | None = None,
) -> str:
    """Register a new PDF in the system. Returns the intake_id.

    The PDF is archived (durable) and moved into the processing directory so a
    later step can read it. `status` controls the lifecycle: production uploads
    use the default "processing"; workbench training samples pass "staged" — they
    are NOT processed until the operator clicks Train. `original_filename`
    overrides the displayed name (the on-disk temp name may carry a uuid prefix
    to avoid collisions, but the operator should see the real filename).
    """
    filename = original_filename or pdf_path.name
    file_size = pdf_path.stat().st_size
    page_count = _get_page_count(pdf_path)

    record = crud.create_intake(
        db,
        original_filename=filename,
        original_pdf_path=str(pdf_path),
        file_size_bytes=file_size,
        page_count=page_count,
        intake_source=intake_source,
        supplier_profile_id=supplier_profile_id,
        account_label=account_label,
    )
    intake_id = record.id

    crud.log_processing_step(
        db, intake_id=intake_id, step="intake", status="started",
        message=f"Received {filename} ({file_size} bytes, {page_count} pages)",
    )

    preserve_source_pdf = intake_source == "watcher" and bool(supplier_profile_id)

    archive_path = _archive_pdf(pdf_path, intake_id)
    record.archive_pdf_path = str(archive_path)

    processing_path = _stage_for_processing(
        pdf_path,
        intake_id,
        preserve_source=preserve_source_pdf,
    )
    record.original_pdf_path = str(processing_path)

    crud.update_intake_status(db, intake_id, status)
    crud.log_processing_step(
        db, intake_id=intake_id, step="intake", status="completed",
        message=("Staged as a training sample" if status == "staged"
                 else "Archived and moved to processing"),
    )

    logger.info("intake.completed", intake_id=intake_id, filename=filename,
                pages=page_count, size=file_size, status=status)

    return intake_id


def _get_page_count(pdf_path: Path) -> int:
    from stencil.extraction.layout import pdf_page_count

    return pdf_page_count(pdf_path)


def _archive_pdf(pdf_path: Path, intake_id: str) -> Path:
    archive_dir = settings.archive_dir / intake_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / "original.pdf"
    shutil.copy2(pdf_path, dest)
    return dest


def _stage_for_processing(pdf_path: Path, intake_id: str, *, preserve_source: bool = False) -> Path:
    proc_dir = settings.processing_dir / intake_id
    proc_dir.mkdir(parents=True, exist_ok=True)
    dest = proc_dir / "original.pdf"
    if preserve_source:
        shutil.copy2(pdf_path, dest)
    else:
        shutil.move(str(pdf_path), str(dest))
    return dest
