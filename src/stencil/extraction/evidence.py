"""Target-to-layout evidence helpers for model authoring."""

from __future__ import annotations

import re
from decimal import Decimal

from stencil.extraction.layout import LayoutCell, LayoutDocument, VisualRow, layout_evidence_summary
from stencil.validation.schema import CanonicalInvoice, LineItem


def build_model_authoring_evidence(
    document: LayoutDocument, invoice: CanonicalInvoice, *, max_rows: int | None = None,
) -> dict:
    """Return compact layout evidence plus deterministic target alignments.

    ``max_rows`` caps the per-line-item evidence to a representative sample (the
    dominant prompt-size term on many-line-item documents); ``None`` aligns all
    rows (legacy behaviour).
    """
    indices: list[int] | None = None
    if max_rows is not None:
        from stencil.models.authoring_context import sample_representative_rows
        indices = sample_representative_rows(invoice, max_rows)
    return {
        "layout": layout_evidence_summary(document),
        "target_alignment": align_invoice_to_layout(document, invoice, line_item_indices=indices),
    }


def align_invoice_to_layout(
    document: LayoutDocument, invoice: CanonicalInvoice, *, line_item_indices: list[int] | None = None,
) -> dict:
    items = invoice.line_items
    if line_item_indices is not None:
        items = [items[i] for i in line_item_indices]
    return {
        "header": _align_header(document, invoice),
        "totals": _align_totals(document, invoice),
        "line_items": [_align_line_item(document, item) for item in items],
    }


def _align_header(document: LayoutDocument, invoice: CanonicalInvoice) -> dict:
    header = invoice.header
    values = {
        "invoice_number": header.invoice_number,
        "account_number": header.account_number,
        "ban": header.ban,
        "currency": header.currency,
        "invoice_date": header.invoice_date.isoformat() if header.invoice_date else None,
        "due_date": header.due_date.isoformat() if header.due_date else None,
    }
    return {name: _find_value_evidence(document, value) for name, value in values.items() if value}


def _align_totals(document: LayoutDocument, invoice: CanonicalInvoice) -> dict:
    values = {
        "subtotal": invoice.subtotal,
        "tax": invoice.tax,
        "fees": invoice.fees,
        "total_due": invoice.total_due,
        "tax_rate": invoice.tax_rate,
    }
    return {name: _find_value_evidence(document, value) for name, value in values.items() if value is not None}


def _align_line_item(document: LayoutDocument, item: LineItem) -> dict:
    fields = {
        "line_number": item.line_number,
        "charge_type": item.charge_type.value if hasattr(item.charge_type, "value") else str(item.charge_type),
        "service_id": _find_value_evidence(document, item.service_id, prefer_row_role="group_total"),
        "billing_reference": _find_value_evidence(document, item.billing_reference, prefer_row_role="group_total"),
        "amount": _find_value_evidence(document, item.amount, prefer_cell_role="amount", prefer_row_role="group_total"),
        "tax_amount": _find_value_evidence(document, item.tax_amount, prefer_cell_role="amount"),
        "quantity": _find_value_evidence(document, item.quantity, prefer_row_role="period_amount"),
        "unit_rate": _find_value_evidence(document, item.unit_rate, prefer_cell_role="amount"),
        "billing_period_start": _find_date_evidence(document, item.billing_period_start),
        "billing_period_end": _find_date_evidence(document, item.billing_period_end),
        "description_parts": _align_description_parts(document, item.description),
        "source_page": item.source_page,
    }
    unresolved = [
        name for name, evidence in fields.items()
        if name not in {"line_number", "charge_type", "source_page", "description_parts"} and evidence is None
    ]
    fields["unresolved_evidence"] = unresolved
    return fields


def _find_value_evidence(
    document: LayoutDocument,
    value,
    *,
    prefer_cell_role: str | None = None,
    prefer_row_role: str | None = None,
) -> dict | None:
    if value is None:
        return None
    needles = _value_needles(value)
    if not needles:
        return None
    matches = []
    for row in _all_rows(document):
        for cell in row.cells:
            if any(_contains_normalized(cell.text, needle) for needle in needles):
                score = 0
                if prefer_cell_role and cell.role == prefer_cell_role:
                    score += 2
                if prefer_row_role and row.row_role == prefer_row_role:
                    score += 3
                matches.append((score, _cell_evidence(row, cell)))
        if any(_contains_normalized(row.text, needle) for needle in needles):
            score = 1 if prefer_row_role and row.row_role == prefer_row_role else 0
            matches.append((score, _row_evidence(row)))
    if not matches:
        return None
    return sorted(matches, key=lambda item: item[0], reverse=True)[0][1]


def _find_date_evidence(document: LayoutDocument, value) -> dict | None:
    if value is None:
        return None
    needles = _value_needles(value)
    return _find_value_evidence(document, next(iter(needles), None), prefer_cell_role="billing_period")


def _align_description_parts(document: LayoutDocument, description: str | None) -> list[dict]:
    if not description:
        return []
    parts = []
    for phrase in re.split(r"\s*;\s*", description):
        phrase = phrase.strip()
        if not phrase:
            continue
        evidence = _find_best_phrase(document, phrase)
        if evidence:
            parts.append({"text": phrase, "evidence": evidence})
    return parts


def _find_best_phrase(document: LayoutDocument, phrase: str) -> dict | None:
    tokens = [t for t in re.findall(r"[A-Za-z0-9]+", phrase.lower()) if len(t) > 2]
    best_score = 0
    best_row = None
    for row in _all_rows(document):
        row_tokens = set(re.findall(r"[A-Za-z0-9]+", row.text.lower()))
        score = sum(1 for token in tokens if token in row_tokens)
        if score > best_score:
            best_score = score
            best_row = row
    return _row_evidence(best_row) if best_row and best_score else None


def _value_needles(value) -> set[str]:
    if isinstance(value, Decimal):
        return {f"{value:.2f}", f"{value:,.2f}", str(value)}
    text = str(value).strip()
    if not text:
        return set()
    needles = {text}
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        y, m, d = text.split("-")
        month = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ][int(m) - 1]
        needles.add(f"{d} {month} {y}")
        needles.add(f"{d}-{month}-{y}")
    return needles


def _contains_normalized(haystack: str, needle: str) -> bool:
    return _norm(needle) in _norm(haystack)


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace(",", "")).strip().lower()


def _all_rows(document: LayoutDocument) -> list[VisualRow]:
    return [row for page in document.pages for row in page.visual_rows]


def _cell_evidence(row: VisualRow, cell: LayoutCell) -> dict:
    return {
        "type": "cell",
        "page": row.page,
        "row_id": row.row_id,
        "row_role": row.row_role,
        "cell_id": cell.cell_id,
        "cell_role": cell.role,
        "column_index": cell.column_index,
        "text": cell.text,
    }


def _row_evidence(row: VisualRow) -> dict:
    return {
        "type": "row",
        "page": row.page,
        "row_id": row.row_id,
        "row_role": row.row_role,
        "text": row.text,
    }
