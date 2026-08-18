"""Coordinate-aware PDF layout extraction.

The deterministic model executor and AI prompts share this representation so
they reason over the same visual rows, cells, and table-like regions instead of
PyMuPDF's flat reading-order text.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import BaseModel, Field

try:  # PyMuPDF is usually imported as fitz; newer wheels also expose pymupdf.
    import fitz  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - depends on installed wheel
    import pymupdf as fitz  # type: ignore


_AMOUNT_RE = re.compile(r"^\(?-?\s*[^\d( -]?\s*\d[\d,]*(?:\.\d{2,})?\s*\)?$")
_ROW_AMOUNT_RE = re.compile(r"\(?-?\s*[^\d\s( -]?\s*\d[\d,]*(?:\.\d{2,})\s*\)?")
_ID_RE = re.compile(r"^[A-Z]{0,6}[-/]?\d[A-Z0-9\-/.]{4,}$", re.IGNORECASE)


class BBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def union(cls, boxes: list[BBox]) -> BBox:
        return cls(
            x0=min(b.x0 for b in boxes),
            y0=min(b.y0 for b in boxes),
            x1=max(b.x1 for b in boxes),
            y1=max(b.y1 for b in boxes),
        )

    def normalized(self, width: float, height: float) -> BBox:
        if width <= 0 or height <= 0:
            return BBox(x0=0, y0=0, x1=0, y1=0)
        return BBox(
            x0=round(self.x0 / width * 1000, 2),
            y0=round(self.y0 / height * 1000, 2),
            x1=round(self.x1 / width * 1000, 2),
            y1=round(self.y1 / height * 1000, 2),
        )


class LayoutWord(BaseModel):
    text: str
    bbox: BBox


class TextBlock(BaseModel):
    page: int
    text: str
    bbox: BBox
    block_type: str = "text"


class LayoutCell(BaseModel):
    cell_id: str = ""
    text: str
    bbox: BBox
    normalized_bbox: BBox | None = None
    row_index: int
    column_index: int
    role: str = "text"
    words: list[LayoutWord] = Field(default_factory=list)


class VisualRow(BaseModel):
    row_id: str = ""
    page: int
    row_index: int
    text: str
    bbox: BBox
    normalized_bbox: BBox | None = None
    row_role: str = "detail"
    cells: list[LayoutCell] = Field(default_factory=list)
    # Which independent reading column this row belongs to on a multi-column
    # ("newspaper") page. 0 for single-column pages (the default), so nothing
    # changes for ordinary layouts. Rows are emitted in per-column reading order
    # (all column-0 rows top-to-bottom, then column-1) when a gutter is detected.
    reading_column: int = 0


class ColumnBand(BaseModel):
    band_id: str
    header_text: str
    role: str
    x0: float
    x1: float
    normalized_x0: float
    normalized_x1: float


class LayoutTable(BaseModel):
    table_id: str = ""
    table_index: int
    pages: list[int]
    bbox: BBox
    normalized_bbox: BBox | None = None
    header_rows: list[VisualRow] = Field(default_factory=list)
    body_rows: list[VisualRow] = Field(default_factory=list)
    column_bands: list[ColumnBand] = Field(default_factory=list)
    source_anchor_text: str | None = None
    confidence: float = 0.5


class LayoutPage(BaseModel):
    page_id: str = ""
    page_number: int
    width: float
    height: float
    text_blocks: list[TextBlock] = Field(default_factory=list)
    visual_rows: list[VisualRow] = Field(default_factory=list)
    tables: list[LayoutTable] = Field(default_factory=list)


class LayoutDocument(BaseModel):
    pages: list[LayoutPage]
    tables: list[LayoutTable] = Field(default_factory=list)
    text: list[str] = Field(default_factory=list)
    markdown: str | None = None
    warnings: list[str] = Field(default_factory=list)


class PageTextScan(BaseModel):
    page_number: int
    text: str
    text_chars: int


class PDFTextScan(BaseModel):
    page_count: int
    text_chars: int
    pages: list[PageTextScan]


def scan_pdf_pages(pdf_path: Path) -> PDFTextScan:
    """Cheap all-page text scan used before coordinate layout extraction."""
    doc = fitz.open(str(pdf_path))
    pages: list[PageTextScan] = []
    try:
        for page_index in range(len(doc)):
            text = doc[page_index].get_text() or ""
            pages.append(PageTextScan(
                page_number=page_index + 1,
                text=text,
                text_chars=len(text),
            ))
    finally:
        doc.close()
    return PDFTextScan(
        page_count=len(pages),
        text_chars=sum(page.text_chars for page in pages),
        pages=pages,
    )


def extract_layout_document(
    pdf_path: Path,
    max_pages: int = 30,
    *,
    include_markdown: bool = True,
    page_numbers: list[int] | None = None,
    column_split_x: list[float] | None = None,
) -> LayoutDocument:
    """Extract coordinate-aware rows/cells and best-effort table regions.

    ``page_numbers`` is one-based and allows discovery/production to avoid
    constructing expensive layout data for irrelevant pages.  Omitting it keeps
    the historical first-``max_pages`` behavior.
    """
    warnings: list[str] = []
    doc = fitz.open(str(pdf_path))
    pages: list[LayoutPage] = []
    all_tables: list[LayoutTable] = []

    if page_numbers is None:
        selected_indexes = list(range(min(max_pages, len(doc))))
    else:
        selected_indexes = sorted({number - 1 for number in page_numbers if 1 <= number <= len(doc)})
        if max_pages > 0:
            selected_indexes = selected_indexes[:max_pages]

    for page_index in selected_indexes:
        page = doc[page_index]
        page_number = page_index + 1
        visual_rows = _visual_rows_for_page(page, page_number, column_split_x)
        text_blocks = _text_blocks_for_page(page, page_number)
        layout_page = LayoutPage(
            page_id=f"p{page_number}",
            page_number=page_number,
            width=float(page.rect.width),
            height=float(page.rect.height),
            text_blocks=text_blocks,
            visual_rows=visual_rows,
        )
        layout_page.tables = _infer_tables_for_page(layout_page, start_index=len(all_tables))
        all_tables.extend(layout_page.tables)
        pages.append(layout_page)

    doc.close()
    markdown = _extract_markdown(pdf_path, warnings) if include_markdown else None
    document = LayoutDocument(pages=pages, tables=all_tables, markdown=markdown, warnings=warnings)
    document.text = render_layout_text(document)
    return document


def render_layout_text(document: LayoutDocument) -> list[str]:
    """Render pages into deterministic, layout-preserving text for AI prompts."""
    rendered = []
    for page in document.pages:
        lines = [
            f"LAYOUT PAGE {page.page_number} ({page.width:.0f}x{page.height:.0f}) "
            f"id={page.page_id} (cell x spans normalized to 0-1000 of page width)"
        ]
        for row in page.visual_rows:
            cell_parts = []
            for cell in row.cells:
                span_box = cell.normalized_bbox or cell.bbox
                span = f"{span_box.x0:.0f}-{span_box.x1:.0f}"
                cell_id = cell.cell_id or f"p{row.page}.r{row.row_index}.c{cell.column_index}"
                cell_parts.append(f"[{cell_id} c{cell.column_index} {cell.role} x={span}] {cell.text}")
            row_id = row.row_id or f"p{row.page}.r{row.row_index}"
            lines.append(f"{row_id} role={row.row_role} " + (" | ".join(cell_parts) if cell_parts else row.text))
        rendered.append("\n".join(lines))
    return rendered


def extract_layout_tables(document: LayoutDocument) -> list[LayoutTable]:
    return document.tables


def layout_evidence_summary(document: LayoutDocument) -> dict:
    """Compact, coordinate-backed evidence for AI model authoring."""
    return {
        "pages": [
            {
                "page_id": page.page_id,
                "page_number": page.page_number,
                "size": {"width": page.width, "height": page.height},
                "rows": [
                    {
                        "row_id": row.row_id,
                        "role": row.row_role,
                        "reading_column": row.reading_column,
                        "text": row.text,
                        "bbox": row.bbox.model_dump(),
                        "normalized_bbox": row.normalized_bbox.model_dump() if row.normalized_bbox else None,
                        "cells": [
                            {
                                "cell_id": cell.cell_id,
                                "column_index": cell.column_index,
                                "role": cell.role,
                                "text": cell.text,
                                "bbox": cell.bbox.model_dump(),
                                "normalized_bbox": (
                                    cell.normalized_bbox.model_dump() if cell.normalized_bbox else None
                                ),
                            }
                            for cell in row.cells
                        ],
                    }
                    for row in page.visual_rows
                    if row.row_role != "noise"
                ],
                "tables": [
                    {
                        "table_id": table.table_id,
                        "anchor": table.source_anchor_text,
                        "pages": table.pages,
                        "bbox": table.bbox.model_dump(),
                        "normalized_bbox": table.normalized_bbox.model_dump() if table.normalized_bbox else None,
                        "header_row_ids": [row.row_id for row in table.header_rows],
                        "body_row_ids": [row.row_id for row in table.body_rows],
                        "column_bands": [band.model_dump() for band in table.column_bands],
                        "confidence": table.confidence,
                    }
                    for table in page.tables
                ],
            }
            for page in document.pages
        ],
        "tables": [
            {
                "table_id": table.table_id,
                "anchor": table.source_anchor_text,
                "pages": table.pages,
                "header_row_ids": [row.row_id for row in table.header_rows],
                "body_row_ids": [row.row_id for row in table.body_rows],
                "column_bands": [band.model_dump() for band in table.column_bands],
            }
            for table in document.tables
        ],
        "warnings": document.warnings,
    }


def _visual_rows_for_page(
    page, page_number: int, column_split_x: list[float] | None = None
) -> list[VisualRow]:
    # Rotated pages are derotated into upright coordinates first, so the same
    # word-clustering path below builds correct rows/columns for them. Never
    # infer rows from PDF text-block boundaries: on a rotated page a block can
    # span a group total AND the next service's identifier.
    matrix = _derotation_matrix(page)
    words = _words_for_page(page, matrix)
    page_width, page_height = _derotated_page_size(page, matrix)

    # Multi-column ("newspaper") pages: when the caller supplies gutter positions
    # (normalized 0-1000, stored on the model's region.column_split_x), cluster
    # each reading column on its own and emit rows in per-column reading order.
    # This is NEVER auto-applied — with no split the page clusters exactly as
    # before, so ordinary single-column layouts are byte-for-byte unchanged.
    gutters_abs = [gx / 1000.0 * page_width for gx in (column_split_x or []) if 0 < gx < 1000]
    if gutters_abs:
        return _visual_rows_multi_column(words, gutters_abs, page_number, page_width, page_height)

    visual_rows = []
    for row_index, row_words in enumerate(_cluster_words_by_y(words)):
        visual_rows.append(
            _build_visual_row(row_words, page_number, row_index, page_width, page_height, 0)
        )
    return visual_rows


def _build_visual_row(
    row_words: list[LayoutWord],
    page_number: int,
    row_index: int,
    page_width: float,
    page_height: float,
    reading_column: int,
) -> VisualRow:
    row_id = f"p{page_number}.r{row_index}"
    cells = _split_row_into_cells(row_words, row_index, row_id, page_width, page_height)
    bbox = BBox.union([word.bbox for word in row_words])
    text = " ".join(word.text for word in row_words)
    return VisualRow(
        row_id=row_id,
        page=page_number,
        row_index=row_index,
        text=text,
        bbox=bbox,
        normalized_bbox=bbox.normalized(page_width, page_height),
        row_role=_infer_row_role(text, cells),
        cells=cells,
        reading_column=reading_column,
    )


def _visual_rows_multi_column(
    words: list[LayoutWord],
    gutters_abs: list[float],
    page_number: int,
    page_width: float,
    page_height: float,
) -> list[VisualRow]:
    """Split words into reading columns at the gutter x's and cluster each on its own.

    Rows are emitted column-by-column (all of column 0 top-to-bottom, then column
    1, ...) with a running ``row_index`` and their ``reading_column`` tagged, so
    the interpreter's region/grouping sees one clean per-column stream instead of
    left- and right-column blocks interleaved by y.
    """
    bounds = sorted(gutters_abs)
    columns: list[list[LayoutWord]] = [[] for _ in range(len(bounds) + 1)]
    for word in words:
        center = (word.bbox.x0 + word.bbox.x1) / 2
        col = 0
        for gx in bounds:
            if center >= gx:
                col += 1
            else:
                break
        columns[col].append(word)

    visual_rows: list[VisualRow] = []
    row_index = 0
    for reading_column, col_words in enumerate(columns):
        if not col_words:
            continue
        for row_words in _cluster_words_by_y(col_words):
            visual_rows.append(
                _build_visual_row(
                    row_words, page_number, row_index, page_width, page_height, reading_column
                )
            )
            row_index += 1
    return visual_rows


def suggest_gutter_for_page(page: LayoutPage) -> float | None:
    """Best-effort column-divider hint for a SINGLE page (normalized 0-1000).

    Scoped to one page on purpose: the builder suggests a divider for the page the
    operator is viewing, never a scan of the whole document — a single-column
    invoice can contain an incidentally two-column page (an address block or
    remittance stub) that a document-wide scan would false-fire on. This is only a
    *hint* the operator confirms, drags, or clears; extraction only ever splits on
    the divider stored in ``region.column_split_x``, so an imperfect guess is
    harmless. ``None`` when no plausible gutter stands out (the common case).
    """
    result = _suggest_gutter_for_page(page)
    return result[1] if result is not None else None


def _suggest_gutter_for_page(page: LayoutPage) -> tuple[float, float] | None:
    """(one_sided_fraction, normalized_gutter_x) for one page, or None.

    A gutter is only proposed where a strong majority of rows live *entirely* on
    one side of it — the trait that separates a two-column "newspaper" layout from
    an ordinary table (whose rows straddle the inter-column gap). The divider is
    placed at the most balanced qualifying x.
    """
    if page.width <= 0:
        return None
    rows: list[list[LayoutWord]] = []
    for row in page.visual_rows:
        row_words = [w for cell in row.cells for w in cell.words]
        if row_words:
            rows.append(row_words)
    if len(rows) < 15:
        return None
    all_words = [w for r in rows for w in r]
    if len(all_words) < 40:
        return None
    xs0 = min(w.bbox.x0 for w in all_words)
    xs1 = max(w.bbox.x1 for w in all_words)
    if xs1 - xs0 < page.width * 0.45:
        return None

    n = len(rows)
    lo = xs0 + 0.35 * (xs1 - xs0)
    hi = xs0 + 0.65 * (xs1 - xs0)
    step = 3.0
    # Precision-biased gate: a hint the user confirms/drags, so we would rather
    # miss (they draw it) than false-fire on a single-column doc. 0.60 clears
    # observed newspaper pages (~0.62-0.69) while rejecting incidental two-column
    # pages in single-column invoices (address/remittance stubs, ~0.58).
    min_one_sided = 0.60
    content_center = (xs0 + xs1) / 2
    best: tuple[int, int, float, float] | None = None  # (|left-right|, crossings, |x-center|, x)
    best_fraction = 0.0
    x = lo
    while x <= hi:
        left = sum(1 for r in rows if all(w.bbox.x1 <= x for w in r))
        right = sum(1 for r in rows if all(w.bbox.x0 >= x for w in r))
        one_sided_fraction = (left + right) / n
        if one_sided_fraction >= min_one_sided and left >= 0.15 * n and right >= 0.15 * n:
            crossings = sum(1 for w in all_words if w.bbox.x0 < x < w.bbox.x1)
            # Prefer a balanced split with an empty gutter, placed centrally.
            key = (abs(left - right), crossings, abs(x - content_center), x)
            if best is None or key < best:
                best = key
                best_fraction = one_sided_fraction
        x += step

    if best is None:
        return None
    return (best_fraction, round(best[3] / page.width * 1000, 2))



def _dominant_text_direction(page) -> tuple[int, int]:
    """The writing direction most of this page's text uses, rounded to an axis."""
    try:
        data = page.get_text("dict")
    except Exception:
        return (1, 0)

    counts: dict[tuple[int, int], int] = {}
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            if not "".join(span.get("text", "") for span in line.get("spans", [])).strip():
                continue
            direction = line.get("dir") or (1, 0)
            try:
                key = (round(float(direction[0])), round(float(direction[1])))
            except (TypeError, ValueError, IndexError):
                continue
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return (1, 0)
    return max(counts.items(), key=lambda item: item[1])[0]


def _derotation_matrix(page):
    """Matrix mapping this page's word coordinates into upright reading space.

    Two distinct cases produce vertical text, and only the first is covered by
    PyMuPDF's own ``rotation_matrix``:

    * ``/Rotate 90|270`` on the page  -> use ``page.rotation_matrix``.
    * text drawn rotated inside a portrait page (``/Rotate 0``, so
      ``rotation_matrix`` is the identity) -> derive one from the dominant
      ``line["dir"]``.

    Returns ``None`` when the page is already upright, so callers can skip the
    per-word transform entirely.
    """
    if getattr(page, "rotation", 0) in (90, 270):
        return page.rotation_matrix

    direction = _dominant_text_direction(page)
    if direction == (0, -1):  # text runs bottom-to-top
        return fitz.Matrix(0, 1, -1, 0, page.rect.height, 0)
    if direction == (0, 1):  # text runs top-to-bottom
        return fitz.Matrix(0, -1, 1, 0, 0, page.rect.width)
    return None


def _derotated_page_size(page, matrix) -> tuple[float, float]:
    """Page (width, height) in the same upright space ``matrix`` maps words into.

    Derived from the ``mediabox`` (always unrotated) rather than ``page.rect``:
    for a ``/Rotate 90`` page ``page.rect`` is *already* the rotated rect, so
    transposing it would be wrong, while for internally-rotated portrait text it
    would be right. Transforming the mediabox handles both.
    """
    if matrix is None:
        return float(page.rect.width), float(page.rect.height)
    bounds = fitz.Rect(page.mediabox) * matrix
    return float(bounds.width), float(bounds.height)


def _words_for_page(page, matrix=None) -> list[LayoutWord]:
    words = []
    for raw in page.get_text("words"):
        text = str(raw[4])
        if not text.strip():
            continue
        bounds = fitz.Rect(raw[:4]) * matrix if matrix is not None else fitz.Rect(raw[:4])
        words.append(
            LayoutWord(
                text=text,
                bbox=BBox(
                    x0=float(bounds.x0),
                    y0=float(bounds.y0),
                    x1=float(bounds.x1),
                    y1=float(bounds.y1),
                ),
            )
        )
    return sorted(words, key=lambda w: (w.bbox.y0, w.bbox.x0))


def _cluster_words_by_y(words: list[LayoutWord], tolerance: float = 3.0) -> list[list[LayoutWord]]:
    centers: list[float] = []
    rows: list[list[LayoutWord]] = []
    for word in words:
        cy = (word.bbox.y0 + word.bbox.y1) / 2
        match_idx = next((i for i, center in enumerate(centers) if abs(cy - center) <= tolerance), None)
        if match_idx is None:
            centers.append(cy)
            rows.append([word])
        else:
            rows[match_idx].append(word)
            centers[match_idx] = (centers[match_idx] * (len(rows[match_idx]) - 1) + cy) / len(rows[match_idx])

    ordered = [row for _, row in sorted(zip(centers, rows), key=lambda item: item[0])]
    return [sorted(row, key=lambda w: w.bbox.x0) for row in ordered]


def _split_row_into_cells(
    words: list[LayoutWord],
    row_index: int,
    row_id: str = "",
    page_width: float = 0,
    page_height: float = 0,
) -> list[LayoutCell]:
    if not words:
        return []
    groups: list[list[LayoutWord]] = [[words[0]]]
    for prev, word in zip(words, words[1:]):
        gap = word.bbox.x0 - prev.bbox.x1
        if gap > 16:
            groups.append([word])
        else:
            groups[-1].append(word)

    cells = []
    for column_index, group in enumerate(groups):
        text = " ".join(word.text for word in group)
        bbox = BBox.union([word.bbox for word in group])
        cells.append(
            LayoutCell(
                cell_id=f"{row_id}.c{column_index}" if row_id else "",
                text=text,
                bbox=bbox,
                normalized_bbox=bbox.normalized(page_width, page_height) if page_width and page_height else None,
                row_index=row_index,
                column_index=column_index,
                role=_infer_cell_role(text),
                words=group,
            )
        )
    return cells


def _infer_cell_role(text: str) -> str:
    value = text.strip()
    if not value:
        return "empty"
    if _parse_decimal(value) is not None and _AMOUNT_RE.match(value) and _looks_like_amount_value(value):
        return "amount"
    if _ID_RE.match(value):
        return "identifier"
    if _parse_decimal(value) is not None and _AMOUNT_RE.match(value):
        return "amount"
    if re.search(r"\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4}\s+-\s+\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4}", value):
        return "billing_period"
    if re.search(
        r"\d{1,2}-[A-Z][a-z]{2}-\d{2,4}\s+\d{1,2}-[A-Z][a-z]{2}-\d{2,4}",
        value,
    ):
        return "billing_period"
    if any(token in value.lower() for token in ("total", "subtotal", "vat", "tax")):
        return "summary"
    return "text"


def _looks_like_amount_value(value: str) -> bool:
    """True for visually amount-like numbers that may also match ID syntax."""
    return bool(re.search(r"\.\d{2,}\)?$", value.strip()) or re.search(r"[$€£,()]", value))


def mark_anchor_rows(document: LayoutDocument, anchor_texts: list[str]) -> LayoutDocument:
    """Mark rows containing any of the given anchor texts with row_role='anchor'.

    Anchor knowledge is supplier configuration (profile hints) or model rules —
    never hardcoded here. Callers that need section anchors (e.g. profile-hint
    normalization) pass their own anchor texts.
    """
    anchors = [anchor.lower() for anchor in anchor_texts if anchor]
    if not anchors:
        return document
    for page in document.pages:
        for row in page.visual_rows:
            low = row.text.lower()
            if any(anchor in low for anchor in anchors):
                row.row_role = "anchor"
    return document


def _infer_row_role(text: str, cells: list[LayoutCell]) -> str:
    """Structural (supplier-agnostic) row role inference.

    Only generic shapes are classified here: header rows, group totals,
    tax/section totals, period+amount rows, identifier-led rows. Supplier
    section anchors are model/profile-owned (see mark_anchor_rows).
    """
    value = text.strip()
    low = value.lower()
    has_amount = any(cell.role == "amount" for cell in cells) or _ROW_AMOUNT_RE.search(value) is not None
    if not value:
        return "noise"
    if _looks_like_header_row(cells):
        return "header"
    if re.match(r"^total\s+[A-Z0-9][A-Z0-9\-/.]{4,}\b", value, re.I) and has_amount:
        return "group_total"
    if re.match(r"^sub\s*-?\s*total\b", value, re.I) and has_amount:
        return "group_total"
    if any(token in low for token in ("total amount due", "current charges", "total current charges")):
        return "section_total"
    if ("vat" in low or "tax" in low) and has_amount:
        return "tax_total"
    if any(cell.role == "billing_period" for cell in cells) and has_amount:
        return "period_amount"
    if cells and cells[0].role == "identifier":
        return "service_start"
    return "detail"


def _looks_like_header_row(cells: list[LayoutCell]) -> bool:
    if len(cells) < 2 or any(cell.role == "amount" for cell in cells):
        return False
    header_terms = {
        "service", "id", "description", "billing", "period", "units", "qty",
        "quantity", "total", "amount", "charge", "date", "rate",
    }
    tokens = set()
    for cell in cells:
        tokens.update(re.findall(r"[a-z]+", cell.text.lower()))
    return len(tokens & header_terms) >= 2


def _parse_decimal(value: str) -> Decimal | None:
    cleaned = re.sub(r"[^\d,.\-()]", "", value.strip())
    if not cleaned:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", "" if re.search(r",\d{3}(?:,|$)", cleaned) else ".")
    try:
        result = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    return -result if negative and result > 0 else result


def _text_blocks_for_page(page, page_number: int) -> list[TextBlock]:
    blocks = []
    try:
        data = page.get_text("dict")
    except Exception:
        return blocks
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        lines = []
        for line in block.get("lines", []):
            spans = [span.get("text", "") for span in line.get("spans", []) if span.get("text")]
            if spans:
                lines.append("".join(spans).strip())
        text = "\n".join(line for line in lines if line)
        if not text:
            continue
        x0, y0, x1, y1 = block.get("bbox", (0, 0, 0, 0))
        blocks.append(TextBlock(page=page_number, text=text, bbox=BBox(x0=x0, y0=y0, x1=x1, y1=y1)))
    return blocks


def _infer_tables_for_page(page: LayoutPage, start_index: int = 0) -> list[LayoutTable]:
    candidate_rows = [
        row for row in page.visual_rows
        if row.row_role in {"anchor", "header", "service_start", "period_amount", "group_total"}
        or (
            len(row.cells) >= 2 and (
                any(cell.role == "amount" for cell in row.cells)
                or any(cell.role == "identifier" for cell in row.cells)
                or any(cell.role == "summary" for cell in row.cells)
            )
        )
    ]
    if not candidate_rows:
        return []

    tables: list[list[VisualRow]] = []
    current: list[VisualRow] = []
    last_y = None
    for row in candidate_rows:
        if last_y is not None and row.bbox.y0 - last_y > 60 and current:
            tables.append(current)
            current = []
        current.append(row)
        last_y = row.bbox.y1
    if current:
        tables.append(current)

    result = []
    for offset, rows in enumerate(tables):
        bbox = BBox.union([row.bbox for row in rows])
        header_rows = [row for row in rows[:5] if row.row_role == "header"]
        if not header_rows:
            header_rows = [row for row in rows[:3] if any(cell.role == "summary" for cell in row.cells)]
        table_index = start_index + offset
        result.append(
            LayoutTable(
                table_id=f"t{table_index}",
                table_index=table_index,
                pages=[page.page_number],
                bbox=bbox,
                normalized_bbox=bbox.normalized(page.width, page.height),
                header_rows=header_rows,
                body_rows=rows,
                column_bands=_column_bands_from_header(header_rows[0], page.width) if header_rows else [],
                source_anchor_text=rows[0].text,
                confidence=0.6,
            )
        )
    return result


def _column_bands_from_header(row: VisualRow, page_width: float) -> list[ColumnBand]:
    bands: list[ColumnBand] = []
    for cell in row.cells:
        bands.append(
            ColumnBand(
                band_id=f"{row.row_id}.b{cell.column_index}",
                header_text=cell.text,
                role=_infer_column_role(cell.text),
                x0=cell.bbox.x0,
                x1=cell.bbox.x1,
                normalized_x0=round(cell.bbox.x0 / page_width * 1000, 2) if page_width else 0,
                normalized_x1=round(cell.bbox.x1 / page_width * 1000, 2) if page_width else 0,
            )
        )
    return bands


def _infer_column_role(text: str) -> str:
    low = text.lower()
    if "service" in low and "id" in low:
        return "service_id"
    if "description" in low or "product" in low:
        return "description"
    if "billing" in low or "period" in low or "date" in low:
        return "billing_period"
    if "unit" in low or "qty" in low or "quantity" in low:
        return "quantity"
    if "total" in low or "amount" in low or "charge" in low:
        return "amount"
    return "text"


def _extract_markdown(pdf_path: Path, warnings: list[str]) -> str | None:
    try:
        import pymupdf4llm  # type: ignore
    except Exception:
        return None
    try:
        return pymupdf4llm.to_markdown(str(pdf_path))
    except Exception as exc:
        warnings.append(f"pymupdf4llm markdown unavailable: {exc}")
        return None
