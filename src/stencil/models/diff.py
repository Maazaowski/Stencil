"""Structured diff between a model's output and the AI ground truth.

The diff is computed at the DELIVERED level (Temforce output rows), because
that is the contract a model must reproduce exactly. The result powers:
- the authoring refine loop (machine-readable feedback to the AI)
- training failure reports persisted for human review
- the golden-corpus regression suite
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from stencil.output.mapper import TEMFORCE_OUTPUT_COLUMNS, output_spec_to_columns
from stencil.output.spec import OutputSpec
from stencil.output.xlsx_writer import build_output_rows
from stencil.validation.schema import CanonicalInvoice

# Default column names (TemForce). The diff is keyed off the columns of whatever
# OutputSpec the invoice will be delivered with — see ``diff_invoices``.
OUTPUT_COLUMN_NAMES = [col.xlsx_header for col in TEMFORCE_OUTPUT_COLUMNS]
_TEXT_COMPARE_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class OutputDiff:
    is_match: bool
    expected_row_count: int
    actual_row_count: int
    missing_rows: list[list] = field(default_factory=list)
    extra_rows: list[list] = field(default_factory=list)
    cell_diffs: list[dict] = field(default_factory=list)
    column_names: list[str] = field(default_factory=lambda: list(OUTPUT_COLUMN_NAMES))

    def summary(self) -> str:
        if self.is_match:
            return "exact match"
        exp, act = self.expected_row_count, self.actual_row_count
        n_missing, n_extra, n_cells = (
            len(self.missing_rows), len(self.extra_rows), len(self.cell_diffs),
        )
        if exp != act:
            # Genuine count problem — keep the explicit "expected X, got Y".
            parts = [f"expected {exp} row(s), got {act}"]
            if n_missing:
                parts.append(f"{n_missing} expected row(s) not produced")
            if n_extra:
                parts.append(f"{n_extra} unexpected row(s) produced")
            if n_cells:
                parts.append(f"{n_cells} cell difference(s)")
            return "; ".join(parts)
        # Same row count: the count is fine, the CONTENT is wrong — say so.
        if n_missing or n_extra:
            mism = max(n_missing, n_extra)
            head = (f"row count matches ({exp}) but {mism} row(s) don't match the "
                    "expected values")
            if n_cells:
                head += f"; {n_cells} cell difference(s)"
            return head
        if n_cells:
            return f"row count matches ({exp}) but {n_cells} cell value(s) differ"
        return "output differs from ground truth"

    def to_report(self) -> dict:
        return {
            "is_match": self.is_match,
            "summary": self.summary(),
            "expected_row_count": self.expected_row_count,
            "actual_row_count": self.actual_row_count,
            "columns": self.column_names,
            "missing_rows": self.missing_rows[:50],
            "extra_rows": self.extra_rows[:50],
            "cell_diffs": self.cell_diffs[:100],
        }

    def feedback(self) -> str:
        """Compact, structured feedback string for the AI refine pass."""
        if self.is_match:
            return "exact match"
        lines = [f"MODEL OUTPUT DOES NOT MATCH GROUND TRUTH: {self.summary()}"]
        if self.missing_rows:
            lines.append("Expected rows the model did NOT produce:")
            for row in self.missing_rows[:10]:
                lines.append("  - " + " | ".join("" if v is None else str(v) for v in row))
        if self.extra_rows:
            lines.append("Rows the model produced that should NOT exist:")
            for row in self.extra_rows[:10]:
                lines.append("  - " + " | ".join("" if v is None else str(v) for v in row))
        if self.cell_diffs:
            lines.append("Cell-level differences (paired rows):")
            for diff in self.cell_diffs[:15]:
                lines.append(
                    f"  - row {diff['row']}, column '{diff['column']}': "
                    f"expected {diff['expected']!r}, got {diff['actual']!r}"
                )
        return "\n".join(lines)


def _row_key(row: list, columns=TEMFORCE_OUTPUT_COLUMNS) -> tuple:
    from stencil.output.mapper import normalize_output_value

    normalized = []
    for col_def, value in zip(columns, row):
        norm = normalize_output_value(value, col_def.canonical_path)
        if isinstance(norm, str):
            norm = _TEXT_COMPARE_WHITESPACE_RE.sub(" ", norm).strip()
        normalized.append("" if norm is None else str(norm))
    return tuple(normalized)


def diff_output_rows(
    expected_rows: list[list],
    actual_rows: list[list],
    columns=TEMFORCE_OUTPUT_COLUMNS,
) -> OutputDiff:
    """Compare two sets of delivered output rows (order-insensitive).

    ``columns`` are the ColumnDefs of the active OutputSpec, so normalization and
    the reported column names match the exact deliverable being produced.
    """
    column_names = [c.xlsx_header for c in columns]

    def key(row: list) -> tuple:
        return _row_key(row, columns)

    expected_sorted = sorted(expected_rows, key=key)
    actual_sorted = sorted(actual_rows, key=key)

    if [key(r) for r in expected_sorted] == [key(r) for r in actual_sorted]:
        return OutputDiff(
            is_match=True,
            expected_row_count=len(expected_rows),
            actual_row_count=len(actual_rows),
            column_names=column_names,
        )

    expected_keys = [key(r) for r in expected_sorted]
    actual_keys = [key(r) for r in actual_sorted]

    # Unmatched rows on each side.
    remaining_actual = list(actual_keys)
    missing: list[list] = []
    for row, key in zip(expected_sorted, expected_keys):
        if key in remaining_actual:
            remaining_actual.remove(key)
        else:
            missing.append(row)
    remaining_expected = list(expected_keys)
    extra: list[list] = []
    for row, key in zip(actual_sorted, actual_keys):
        if key in remaining_expected:
            remaining_expected.remove(key)
        else:
            extra.append(row)

    # Pair up the unmatched rows positionally to surface cell-level diffs.
    cell_diffs: list[dict] = []
    for idx, (exp, act) in enumerate(zip(missing, extra)):
        for col_idx, (e, a) in enumerate(zip(exp, act)):
            if ("" if e is None else str(e)) != ("" if a is None else str(a)):
                cell_diffs.append({
                    "row": idx,
                    "column": column_names[col_idx] if col_idx < len(column_names) else str(col_idx),
                    "expected": e,
                    "actual": a,
                })

    return OutputDiff(
        is_match=False,
        expected_row_count=len(expected_rows),
        actual_row_count=len(actual_rows),
        missing_rows=missing,
        extra_rows=extra,
        cell_diffs=cell_diffs,
        column_names=column_names,
    )


def diff_invoices(
    expected: CanonicalInvoice,
    actual: CanonicalInvoice,
    spec: OutputSpec | None = None,
) -> OutputDiff:
    """Diff two canonical invoices at the delivered-output level.

    ``spec`` is the OutputSpec the invoice will be delivered with; the model is
    therefore validated against the EXACT columns it will produce, not a global
    constant. Omitting it falls back to the built-in TemForce layout.
    """
    columns = output_spec_to_columns(spec) if spec is not None else TEMFORCE_OUTPUT_COLUMNS
    expected_rows = build_output_rows(expected, spec)
    actual_rows = build_output_rows(actual, spec)
    return diff_output_rows(expected_rows, actual_rows, columns)
