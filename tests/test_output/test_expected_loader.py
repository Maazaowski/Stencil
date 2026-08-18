"""Offline tests for the XLS/XLSX blueprint loader used in profile authoring."""

import sys
from pathlib import Path
from types import SimpleNamespace

from openpyxl import Workbook

from stencil.models.diff import diff_output_rows
from stencil.output.expected_loader import load_blueprint_context, load_expected_rows
from stencil.output.mapper import output_spec_to_columns
from stencil.specs.loader import default_output_spec


def _write_xlsx(path: Path, headers: list[str], rows: list[list]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.save(str(path))


def test_load_expected_rows_aligns_by_header(tmp_path):
    spec = default_output_spec()
    headers = [c.header for c in spec.columns]
    rows = [
        ["SVC1", "REF1", "2026-01-01", "2026-02-01", "100.00", "ACC1", "INV1", "8.25"],
        [None] * len(headers),  # blank row should be dropped
        ["SVC2", "REF2", "2026-01-01", "2026-02-01", "50.00", "ACC1", "INV1", "4.13"],
    ]
    xlsx = tmp_path / "expected.xlsx"
    _write_xlsx(xlsx, headers, rows)

    loaded = load_expected_rows(xlsx, spec)
    assert len(loaded) == 2
    assert loaded[0][0] == "SVC1"
    assert loaded[1][4] == "50.00"

    # Aligned rows diff cleanly against themselves through the diff engine.
    diff = diff_output_rows(loaded, loaded, output_spec_to_columns(spec))
    assert diff.is_match


def test_load_expected_rows_reordered_columns(tmp_path):
    """Columns in a different order than the spec are realigned by header text."""
    spec = default_output_spec()
    headers = [c.header for c in spec.columns]
    reordered = list(reversed(headers))
    # Build a row keyed by header so we can reorder values to match.
    canonical = {
        headers[0]: "SVC1", headers[1]: "REF1", headers[2]: "2026-01-01",
        headers[3]: "2026-02-01", headers[4]: "100.00", headers[5]: "ACC1",
        headers[6]: "INV1", headers[7]: "8.25",
    }
    xlsx = tmp_path / "reordered.xlsx"
    _write_xlsx(xlsx, reordered, [[canonical[h] for h in reordered]])

    loaded = load_expected_rows(xlsx, spec)
    assert loaded[0][0] == "SVC1"  # realigned back to spec order
    assert loaded[0][4] == "100.00"


def test_load_blueprint_context_includes_headers_samples_and_totals(tmp_path):
    spec = default_output_spec()
    headers = [c.header for c in spec.columns]
    xlsx = tmp_path / "blueprint.xlsx"
    _write_xlsx(xlsx, headers, [
        ["SVC1", "REF1", "2026-01-01", "2026-02-01", "100.00", "ACC1", "INV1", "8.25"],
        ["SVC2", "REF2", "2026-01-01", "2026-02-01", "50.00", "ACC1", "INV1", "4.13"],
    ])

    context = load_blueprint_context(xlsx, spec, sample_rows=1)

    assert context["sheet_name"] == "Sheet"
    assert context["raw_headers"] == headers
    assert context["row_count"] == 2
    assert context["sample_rows"] == [context["aligned_rows"][0]]
    assert context["totals"] == {"amount": "150.00", "tax": "12.38"}
    assert context["warnings"] == []
    assert context["contract_compatible"] is True


def test_load_blueprint_context_rejects_nonempty_header_mismatch(tmp_path):
    spec = default_output_spec()
    xlsx = tmp_path / "positional.xlsx"
    _write_xlsx(xlsx, ["A", "B", "C", "D", "E", "F", "G", "H"], [
        ["SVC1", "REF1", "2026-01-01", "2026-02-01", "100.00", "ACC1", "INV1", "8.25"],
    ])

    context = load_blueprint_context(xlsx, spec)

    assert context["contract_compatible"] is False
    assert context["aligned_rows"] == []
    assert "No blueprint headers matched" in context["warnings"][0]


def test_load_blueprint_context_supports_legacy_xls(monkeypatch, tmp_path):
    spec = default_output_spec()
    xls = tmp_path / "legacy.xls"
    xls.write_bytes(b"not read by fake xlrd")
    headers = [c.header for c in spec.columns]
    rows = [headers, ["SVC1", "REF1", "2026-01-01", "2026-02-01", "100.00", "ACC1", "INV1", "8.25"]]

    class FakeSheet:
        name = "Legacy"
        nrows = len(rows)
        ncols = len(headers)

        def cell_value(self, row, col):
            return rows[row][col]

    fake_xlrd = SimpleNamespace(open_workbook=lambda _path: SimpleNamespace(sheet_by_index=lambda _idx: FakeSheet()))
    monkeypatch.setitem(sys.modules, "xlrd", fake_xlrd)

    context = load_blueprint_context(xls, spec)

    assert context["sheet_name"] == "Legacy"
    assert context["aligned_rows"][0][0] == "SVC1"
    assert context["totals"] == {"amount": "100.00", "tax": "8.25"}
