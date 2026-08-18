"""Build a JSON-friendly preview of a deliverable from an extracted document.

This is the single source of truth for in-app output preview. It renders the
EXACT delivered columns/rows (via the same row builder the XLSX writer uses) plus
a small header-fields summary and reconciliation status, in a shape the frontend
table can consume directly. Used by three surfaces: a processed invoice, the
profile config preview (AI extraction), and the model workbench (model replay).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from stencil.output.formatting import format_deliverable_date
from stencil.output.mapper import output_spec_to_columns
from stencil.output.spec import OutputSpec
from stencil.output.xlsx_writer import build_output_rows, service_line_items
from stencil.validation.schema import ExtractedDocument

# Document-level fields shown in the preview header card, in display order.
_HEADER_DISPLAY: list[tuple[str, str]] = [
    ("supplier_name", "Supplier"),
    ("invoice_number", "Invoice Number"),
    ("invoice_date", "Invoice Date"),
    ("due_date", "Due Date"),
    ("account_number", "Account Number"),
    ("currency", "Currency"),
]

# Totals shown beneath the header, only when present on the document.
_TOTALS_DISPLAY: list[tuple[str, str]] = [
    ("subtotal", "Subtotal"),
    ("tax", "Tax"),
    ("current_charges", "Current Charges"),
    ("total_due", "Total Due"),
]


def _json_safe(value: Any) -> Any:
    """Coerce a cell/value to something JSON-serializable for the API."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return format_deliverable_date(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def build_preview(invoice: ExtractedDocument, spec: OutputSpec | None = None) -> dict[str, Any]:
    """Render an extracted document as a preview payload.

    ``columns`` + ``rows`` mirror the delivered XLSX exactly (same OutputSpec,
    same computed fields). ``header_fields`` and ``totals`` are display label/value
    pairs; ``reconciliation`` summarizes the stated-vs-computed total check.
    """
    columns = output_spec_to_columns(spec) if spec is not None else None
    column_meta = [
        {"header": c.xlsx_header, "number_format": c.number_format}
        for c in (columns or _temforce_columns())
    ]

    raw_rows = build_output_rows(invoice, spec)
    rows = [[_json_safe(cell) for cell in row] for row in raw_rows]

    header = invoice.header
    # ``name`` is the canonical field key (not just the display label) so callers
    # can correlate an entry with the rule that produced it — the model builder
    # uses it to tell "configured but not found" from "never configured".
    header_fields = [
        {"name": name, "label": label, "value": _json_safe(getattr(header, name, None))}
        for name, label in _HEADER_DISPLAY
    ]
    totals = [
        {"name": name, "label": label, "value": _json_safe(getattr(invoice, name, None))}
        for name, label in _TOTALS_DISPLAY
        if getattr(invoice, name, None) is not None
    ]

    return {
        "columns": column_meta,
        "rows": rows,
        "row_count": len(rows),
        "line_item_count": len(service_line_items(invoice)),
        "header_fields": header_fields,
        "totals": totals,
        "reconciliation": _reconciliation_summary(invoice),
        # Stashed by the row builder above: which tax strategy actually delivered
        # EXT_TAX (per_line / allocated / rate / none).
        "tax_method": invoice.fields.get("_tax_method"),
    }


def preview_for_profile(
    invoice: ExtractedDocument,
    profile,
    *,
    extraction_path: str,
    output_spec_id: str | None = None,
    field_schema_id: str | None = None,
) -> dict[str, Any]:
    """Build an ad-hoc preview for a freshly extracted document under a profile.

    Runs the same deterministic reconciliation the pipeline does so the preview's
    reconciliation banner matches production, then renders through the profile's
    OutputSpec. Used by the profile (AI) and model (replay) preview endpoints.

    ``output_spec_id`` / ``field_schema_id`` apply ONLY when there is no profile:
    the model builder can author against a deliverable the user picked directly,
    before any profile exists. A profile always wins over them, so the preview
    can never disagree with what that profile will actually deliver.
    """
    from stencil.fields.loader import (
        default_field_schema,
        get_field_schema,
        resolve_merged_field_schema,
    )
    from stencil.pipeline.processor import (
        _keep_zero_line_items,
        _normalize_line_items,
        _require_identifier,
    )
    from stencil.specs.loader import default_output_spec, get_output_spec, resolve_output_spec
    from stencil.validation.reconciler import reconcile

    if profile is not None:
        schema = resolve_merged_field_schema(profile)
    elif field_schema_id:
        schema = get_field_schema(field_schema_id)
    else:
        schema = default_field_schema()
    # Mirror the pipeline: drop zero-amount / no-identifier rows (per profile) and
    # resequence, so the preview matches what production actually delivers.
    _normalize_line_items(
        invoice,
        keep_zero_amount=_keep_zero_line_items(profile),
        require_identifier=_require_identifier(profile),
    )
    if invoice.reconciliation is None:
        recon = reconcile(invoice, schema)
        if recon is not None:
            invoice.reconciliation = recon
            invoice.warnings.extend(w for w in recon.warnings if w not in invoice.warnings)

    if profile is not None:
        spec = resolve_output_spec(profile)
    elif output_spec_id:
        spec = get_output_spec(output_spec_id)
    else:
        spec = default_output_spec()
    payload = build_preview(invoice, spec)
    payload["extraction_path"] = extraction_path
    payload["warnings"] = list(invoice.warnings or [])

    # Setup conflicts are a pure function of the profile + schema, so recompute
    # them here rather than plumbing raw extraction data through the preview.
    if profile is not None:
        from stencil.extraction.instructions import compile_instructions

        compiled = compile_instructions(
            supplier_profile=profile.model_dump(),
            field_schema=schema,
            output_type=profile.classification.output_type,
            supplier_name=profile.identity.canonical_name,
        )
        compiled_payload = compiled.as_dict()
        payload["conflicts"] = compiled_payload["conflicts"]
        payload["ignored_notes"] = compiled_payload["ignored_notes"]
    return payload


def _temforce_columns():
    from stencil.output.mapper import TEMFORCE_OUTPUT_COLUMNS

    return TEMFORCE_OUTPUT_COLUMNS


def _reconciliation_summary(invoice: ExtractedDocument) -> dict[str, Any] | None:
    rec = invoice.reconciliation
    if rec is None:
        return None
    return {
        "is_reconciled": rec.is_reconciled,
        "verification_status": rec.verification_status,
        "line_items_sum": _json_safe(rec.line_items_sum),
        "reconcile_target": _json_safe(rec.reconcile_target),
        "stated_total": _json_safe(rec.stated_total),
        "computed_total": _json_safe(rec.computed_total),
        "variance": _json_safe(rec.variance),
        "variance_pct": rec.variance_pct,
        "warnings": list(rec.warnings),
    }
