"""Exception handler — creates exception packages for failed or uncertain invoices."""

import json
import shutil
from datetime import datetime
from pathlib import Path

import structlog

from stencil.config import settings
from stencil.output.formatting import format_deliverable_invoice_dates
from stencil.validation.schema import CanonicalInvoice

logger = structlog.get_logger()


class ExceptionReason:
    UNKNOWN_SUPPLIER = "unknown_supplier"
    LOW_CONFIDENCE = "low_confidence"
    NO_LINE_ITEMS = "no_line_items"
    RECONCILIATION_FAILURE = "reconciliation_failure"
    LAYOUT_DRIFT = "layout_drift"
    UNREADABLE_PDF = "unreadable_pdf"
    SCHEMA_VALIDATION_FAILURE = "schema_validation_failure"
    EXTRACTION_ERROR = "extraction_error"
    MISSING_SERVICE_ID = "missing_service_id"
    PROFILE_VALIDATION_FAILURE = "profile_validation_failure"


def create_exception_package(
    intake_id: str,
    reason: str,
    message: str,
    pdf_path: Path | None = None,
    partial_invoice: CanonicalInvoice | None = None,
    error_details: dict | None = None,
) -> Path:
    """Create an exception package in the exceptions directory."""
    exception_dir = settings.exceptions_dir / intake_id
    exception_dir.mkdir(parents=True, exist_ok=True)

    if pdf_path and pdf_path.exists():
        shutil.copy2(pdf_path, exception_dir / "original.pdf")

    if partial_invoice:
        partial_path = exception_dir / "partial_extraction.json"
        partial_data = format_deliverable_invoice_dates(partial_invoice.model_dump(mode="json"))
        partial_path.write_text(
            json.dumps(partial_data, indent=2, default=str),
            encoding="utf-8",
        )

    error_log = {
        "intake_id": intake_id,
        "reason": reason,
        "message": message,
        "details": error_details or {},
        "timestamp": datetime.now().isoformat(),
    }
    error_path = exception_dir / "error_log.json"
    error_path.write_text(
        json.dumps(error_log, indent=2, default=str),
        encoding="utf-8",
    )

    logger.warning("exception.created", intake_id=intake_id, reason=reason,
                   message=message)

    return exception_dir
