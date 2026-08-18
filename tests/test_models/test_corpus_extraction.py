"""Suite 2 — Model extraction correctness across the golden corpus.

Deterministic, no AI, runs on every change:
- Load the committed model.json snapshot for each layout
- Execute it via the generic interpreter against EVERY PDF in that folder
- Build output rows and compare against the corresponding expectations:
  * expected/{pdf_stem}.xlsx     -> exact cell-by-cell comparison
  * expected_line_items.json     -> summary comparison (count / total / ids)

Layouts without a committed model.json are skipped (the snapshot is produced
by Suite 3 / the training workflow and committed once authoring succeeds).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pytest

from stencil.models.interpreter import execute_model
from stencil.models.training import _normalize_line_items
from stencil.output.formatting import format_deliverable_date
from stencil.output.mapper import TEMFORCE_OUTPUT_COLUMNS
from stencil.output.xlsx_writer import build_output_rows
from tests.corpus_utils import CorpusLayout, layout_params, read_expected_xlsx_rows

OUTPUT_HEADERS = [column.xlsx_header for column in TEMFORCE_OUTPUT_COLUMNS]
SERVICE_ID_COL = OUTPUT_HEADERS.index("EXT_SERVICEID")
AMOUNT_COL = OUTPUT_HEADERS.index("EXT_AMOUNT")


def _canon(value) -> str:
    """Canonical comparable form for one output cell."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        formatted = format_deliverable_date(value)
        return formatted or ""
    if isinstance(value, date):
        formatted = format_deliverable_date(value)
        return formatted or ""
    text = str(value).strip()
    try:
        decimal_value = Decimal(text.replace(",", ""))
    except (InvalidOperation, ValueError):
        return text
    return str(decimal_value.normalize())


def _run_model(layout: CorpusLayout, pdf):
    model = layout.load_model()
    profile = layout.load_profile() if layout.profile_path.exists() else None
    invoice = execute_model(model, pdf, intake_id=f"corpus-{pdf.stem}")
    _normalize_line_items(
        invoice,
        keep_zero_amount=profile.include_zero_amount_line_items if profile else False,
    )
    rows = build_output_rows(invoice)
    return rows


def _compare_to_xlsx(layout: CorpusLayout, pdf, rows: list[list]) -> list[str]:
    expected_rows = read_expected_xlsx_rows(layout.expected_xlsx_for(pdf))
    errors: list[str] = []
    if len(rows) != len(expected_rows):
        errors.append(f"row count: expected {len(expected_rows)}, got {len(rows)}")
    for row_idx, expected in enumerate(expected_rows):
        if row_idx >= len(rows):
            break
        actual = rows[row_idx]
        for col_idx, header in enumerate(OUTPUT_HEADERS):
            if header not in expected:
                continue
            expected_value = _canon(expected[header])
            actual_value = _canon(actual[col_idx]) if col_idx < len(actual) else ""
            if expected_value != actual_value:
                errors.append(
                    f"row {row_idx + 1} [{header}]: expected {expected_value!r}, got {actual_value!r}"
                )
    return errors


def _compare_to_summary(expectation: dict, rows: list[list]) -> list[str]:
    errors: list[str] = []
    expected_count = expectation.get("count")
    if expected_count is not None and len(rows) != expected_count:
        errors.append(f"row count: expected {expected_count}, got {len(rows)}")
    expected_total = expectation.get("total")
    if expected_total is not None:
        actual_total = sum((Decimal(str(row[AMOUNT_COL])) for row in rows), Decimal("0"))
        if actual_total != Decimal(expected_total):
            errors.append(f"amount total: expected {expected_total}, got {actual_total}")
    expected_ids = expectation.get("service_ids")
    if expected_ids is not None:
        actual_ids = sorted(str(row[SERVICE_ID_COL]) for row in rows)
        if actual_ids != sorted(expected_ids):
            errors.append(f"service ids: expected {sorted(expected_ids)}, got {actual_ids}")
    return errors


@pytest.mark.parametrize("layout", layout_params())
def test_model_extracts_corpus_invoices_correctly(layout: CorpusLayout):
    if not layout.model_path.exists():
        pytest.skip(f"{layout.layout_id}: no model.json snapshot committed yet")

    summary = (
        layout.load_summary_expectations().get("invoices", {})
        if layout.summary_expectations_path.exists()
        else {}
    )

    failures: list[str] = []
    compared = 0
    for pdf in layout.pdfs:
        rows = _run_model(layout, pdf)

        if layout.expected_xlsx_for(pdf).exists():
            errors = _compare_to_xlsx(layout, pdf, rows)
            compared += 1
        elif pdf.name in summary:
            errors = _compare_to_summary(summary[pdf.name], rows)
            compared += 1
        else:
            continue

        if errors:
            failures.append(f"{pdf.name}:\n  " + "\n  ".join(errors))

    if compared == 0:
        pytest.skip(f"{layout.layout_id}: no expected XLSX or summary expectations found")

    assert not failures, (
        f"Layout '{layout.layout_id}': model output does not match expectations:\n"
        + "\n".join(failures)
    )
