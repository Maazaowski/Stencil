"""Side-by-side AI vs model outputs for human review."""

from __future__ import annotations

import json

import structlog

from stencil.config import settings
from stencil.output.formatting import format_deliverable_invoice_dates
from stencil.output.spec import OutputSpec
from stencil.output.xlsx_writer import write_xlsx
from stencil.validation.schema import CanonicalInvoice

logger = structlog.get_logger()


def write_model_review_artifacts(
    intake_id: str,
    ai_invoice: CanonicalInvoice,
    model_invoice: CanonicalInvoice | None = None,
    *,
    execution_error: str | None = None,
    spec: OutputSpec | None = None,
) -> list[str]:
    """Write comparison files under completed/{intake_id}/model_review/.

    ``spec`` is the OutputSpec both sides are rendered with so the side-by-side
    XLSX matches the real deliverable. Best-effort: never raises. Returns
    filenames written.
    """
    review_root = settings.completed_dir / intake_id / "model_review"
    written: list[str] = []
    try:
        review_root.mkdir(parents=True, exist_ok=True)

        try:
            write_xlsx(ai_invoice, review_root / "ai_output.xlsx", spec)
            written.append("ai_output.xlsx")
        except Exception as exc:
            logger.warning("review.ai_xlsx_failed", intake_id=intake_id, error=str(exc))

        if model_invoice is not None:
            write_xlsx(model_invoice, review_root / "model_output.xlsx", spec)
            written.append("model_output.xlsx")
            model_data = format_deliverable_invoice_dates(model_invoice.model_dump(mode="json"))
            (review_root / "model_output.json").write_text(
                json.dumps(model_data, indent=2, default=str),
                encoding="utf-8",
            )
            written.append("model_output.json")
        if execution_error:
            (review_root / "execution_error.txt").write_text(
                execution_error + "\n", encoding="utf-8",
            )
            written.append("execution_error.txt")
    except Exception as exc:
        logger.warning("review.write_failed", intake_id=intake_id, error=str(exc))
    return written
