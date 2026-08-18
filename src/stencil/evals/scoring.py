"""Scorers — turn produced output + ground truth into a metrics dict.

Composes existing primitives: ``diff_output_rows`` for deliverable row/cell
correctness, the preview's reconciliation for value-level consistency, and a
grounding check against the source layout text for hallucinations.
"""

from __future__ import annotations

import re

from stencil.models.diff import diff_output_rows
from stencil.output.mapper import normalize_output_value

_GROUND_NORMALIZE = re.compile(r"[\s,\-]+")
_AMOUNT_RE = re.compile(r"^-?\d[\d,]*\.\d{2}$")


def expected_dicts_to_lists(expected_rows: list[dict], columns) -> list[list]:
    """Align expected XLSX dict-rows to the deliverable column order for diffing."""
    headers = [c.xlsx_header for c in columns]
    return [[row.get(h) for h in headers] for row in expected_rows]


def cell_diff_summary(cell_diffs: list[dict], limit: int = 8) -> list[dict]:
    """Collapse per-cell diffs into one entry per column: how many rows it hit,
    plus a representative expected/actual pair.

    Row matching is whole-row (see ``diff._row_key``), so a single systematically
    wrong column — a day/month-swapped date, say — scores zero and emits one diff
    per row. Hundreds of identical lines bury the cause; the column name with a
    count and one example is the diagnosis.
    """
    by_column: dict[str, dict] = {}
    for entry in cell_diffs:
        column = str(entry.get("column", ""))
        summary = by_column.setdefault(
            column,
            {
                "column": column,
                "rows": 0,
                "expected": entry.get("expected"),
                "actual": entry.get("actual"),
            },
        )
        summary["rows"] += 1
    return sorted(by_column.values(), key=lambda e: e["rows"], reverse=True)[:limit]


def diff_metrics(expected_rows: list[dict], actual_rows: list[list], columns) -> dict:
    """Row precision/recall/F1, cell diffs, and the raw missing/extra rows."""
    expected_lists = expected_dicts_to_lists(expected_rows, columns)
    diff = diff_output_rows(expected_lists, actual_rows, columns)

    expected_n = len(expected_lists)
    actual_n = len(actual_rows)
    missing_n = len(diff.missing_rows)
    extra_n = len(diff.extra_rows)
    matched = expected_n - missing_n  # rows present in both by key

    precision = matched / actual_n if actual_n else (1.0 if expected_n == 0 else 0.0)
    recall = matched / expected_n if expected_n else (1.0 if actual_n == 0 else 0.0)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return {
        "is_match": diff.is_match,
        "expected_rows": expected_n,
        "actual_rows": actual_n,
        "matched_rows": matched,
        "missing_rows": missing_n,
        "extra_rows": extra_n,
        "row_precision": round(precision, 4),
        "row_recall": round(recall, 4),
        "row_f1": round(f1, 4),
        "cell_diffs": diff.cell_diffs[:200],
        # Summarized from the FULL diff, not the truncated list above, so the
        # per-column row counts are real rather than capped at 200.
        "cell_diff_summary": cell_diff_summary(diff.cell_diffs),
        "missing_examples": [list(r) for r in diff.missing_rows[:50]],
        "extra_examples": [list(r) for r in diff.extra_rows[:50]],
    }


def _normalize_ground(text: str) -> str:
    return _GROUND_NORMALIZE.sub("", str(text)).lower()


def _is_groundable(value) -> bool:
    text = str(value).strip()
    if not text:
        return False
    if _AMOUNT_RE.match(text):
        return True
    # identifier-like: >=6 chars, has a digit (service numbers, account ids)
    return len(text) >= 6 and any(c.isdigit() for c in text)


def hallucination_flags(
    actual_rows: list[list],
    columns,
    source_text: str,
    expected_rows: list[list] | None = None,
) -> list[dict]:
    """Flag emitted identifier/amount cells whose value is absent from the source.

    Grounding is space/comma/dash-insensitive, so '100-502-0019' matches the raw
    text and '1,455.10' matches '1455.10'. A miss is a likely hallucination.
    """
    haystack = _normalize_ground(source_text)
    headers = [c.xlsx_header for c in columns]
    expected_values = _expected_values_by_column(expected_rows or [], columns)
    flags: list[dict] = []
    for row_idx, row in enumerate(actual_rows):
        for col_idx, cell in enumerate(row):
            if col_idx >= len(columns):
                continue
            canonical_path = columns[col_idx].canonical_path if col_idx < len(columns) else ""
            if canonical_path == "computed.line_tax" or canonical_path.endswith((
                ".invoice_date", ".due_date", ".billing_period_start", ".billing_period_end",
            )):
                continue
            if not _is_groundable(cell):
                continue
            if _normalized_cell(cell, columns[col_idx]) in expected_values.get(col_idx, set()):
                continue
            if _normalize_ground(cell) not in haystack:
                flags.append({
                    "row": row_idx,
                    "column": headers[col_idx] if col_idx < len(headers) else str(col_idx),
                    "value": str(cell),
                })
    return flags


def _expected_values_by_column(expected_rows: list[list], columns) -> dict[int, set[str]]:
    values: dict[int, set[str]] = {}
    for row in expected_rows:
        for col_idx, cell in enumerate(row):
            if col_idx >= len(columns):
                continue
            values.setdefault(col_idx, set()).add(_normalized_cell(cell, columns[col_idx]))
    return values


def _normalized_cell(value, column) -> str:
    normalized = normalize_output_value(value, column.canonical_path)
    return "" if normalized is None else str(normalized).strip().lower()


def consistency_from_preview(preview: dict) -> dict:
    """Reconciliation summary (stated vs computed total) as a consistency metric."""
    recon = preview.get("reconciliation") or {}
    return {
        "is_reconciled": bool(recon.get("is_reconciled")),
        "variance": recon.get("variance"),
        "variance_pct": recon.get("variance_pct"),
        "warnings": recon.get("warnings") or [],
    }


def classification_metrics(expected: dict, actual: dict) -> dict:
    supplier_ok = _eq(expected.get("supplier_name"), actual.get("supplier_name"))
    output_ok = _eq(expected.get("output_type"), actual.get("output_type"))
    return {
        "supplier_match": supplier_ok,
        "output_type_match": output_ok,
        "is_match": supplier_ok and output_ok,
        "expected": expected,
        "actual": actual,
    }


def _eq(a, b) -> bool:
    return str(a or "").strip().lower() == str(b or "").strip().lower()
