"""Intake- and sample-scoped endpoints that power the visual model builder.

The builder authors a model against a *sample* PDF the user uploads (it is not
tied to a processed invoice). Samples are stored under ``WORK_DIR/builder_samples``
and referenced by a ``sample_id`` that works anywhere an ``intake_id`` does:
the coordinate-aware layout (to draw on), the draft dry-run, and model create
all resolve their PDF through the same helper.
"""

from pathlib import Path
from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from stencil.config import settings

logger = structlog.get_logger()

router = APIRouter(prefix="/intakes", tags=["intakes"])


def builder_samples_dir() -> Path:
    return settings.work_dir / "builder_samples"


def resolve_intake_pdf_path(intake_id: str) -> Path:
    """Locate a PDF by intake_id OR builder sample_id.

    Searches the processed-invoice lifecycle dirs and the builder sample store,
    so the layout / draft-test / create endpoints all accept either id. Raises
    HTTPException(404) when no PDF exists.
    """
    for base in (
        settings.archive_dir,
        settings.processing_dir,
        settings.completed_dir,
        builder_samples_dir(),
    ):
        if base is None:
            continue
        candidate = base / intake_id / "original.pdf"
        if candidate.exists():
            return candidate
    raise HTTPException(status_code=404, detail="Source PDF not found")


@router.post("/samples")
async def create_builder_sample(file: UploadFile):
    """Upload a sample invoice PDF to author/preview a model against. Returns a
    sample_id usable with the layout, draft-test, and model-create endpoints."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    safe_name = Path(file.filename).name
    if safe_name != file.filename or safe_name in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid filename")

    sample_id = uuid4().hex
    dest_dir = builder_samples_dir() / sample_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "original.pdf").write_bytes(await file.read())
    (dest_dir / "filename.txt").write_text(safe_name, encoding="utf-8")
    logger.info("builder.sample_uploaded", sample_id=sample_id, filename=safe_name)
    return {"sample_id": sample_id, "filename": safe_name}


@router.get("/samples/{sample_id}/pdf")
def get_builder_sample_pdf(sample_id: str):
    """Serve the raw sample PDF so the builder can show the original alongside its
    text reconstruction — a diagnostic reference when reconstructed cells look
    truncated or mis-split. Served inline so it embeds in an iframe."""
    safe_id = Path(sample_id).name
    if safe_id != sample_id or safe_id in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid sample id")
    pdf_path = builder_samples_dir() / safe_id / "original.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Sample PDF not found")
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"{safe_id}.pdf",
        content_disposition_type="inline",
    )


@router.get("/{intake_id}/layout")
def get_intake_layout(
    intake_id: str,
    column_split_x: list[float] | None = Query(default=None),
    max_pages: int = Query(default=30, ge=1),
):
    """Coordinate-aware layout of the invoice/sample: pages (size), visual rows
    and cells with normalized bboxes + inferred roles. Drives the builder canvas.

    ``column_split_x`` (normalized 0-1000 gutters) re-reads a two-column page as
    independent reading columns, so the canvas shows the same rows the model will
    execute against. With no split each page carries a best-effort
    ``suggested_column_split_x`` hint for the builder's divider.

    ``max_pages`` bounds the canvas payload -- a 700-page document would otherwise
    serialize every cell on every page. ``page_count`` reports the document's true
    length and ``rendered_page_count`` what this response actually carries; any
    truncation is also named in ``warnings``.
    """
    from stencil.extraction.layout import (
        extract_layout_document,
        layout_evidence_summary,
        pdf_page_count,
        suggest_gutter_for_page,
    )

    pdf_path = resolve_intake_pdf_path(intake_id)
    try:
        document = extract_layout_document(
            pdf_path,
            max_pages=max_pages,
            include_markdown=False,
            column_split_x=column_split_x or None,
        )
    except Exception as e:  # pragma: no cover - defensive
        raise HTTPException(status_code=422, detail=f"Layout extraction failed: {e}") from e

    summary = layout_evidence_summary(document)
    # The hint is only meaningful on unsplit rows; once a split is applied the
    # builder already has the divider it asked for.
    if not column_split_x:
        for page, page_summary in zip(document.pages, summary["pages"], strict=False):
            page_summary["suggested_column_split_x"] = suggest_gutter_for_page(page)

    return {
        "intake_id": intake_id,
        "page_count": pdf_page_count(pdf_path),
        "rendered_page_count": len(document.pages),
        "column_split_x": column_split_x or [],
        **summary,
    }
