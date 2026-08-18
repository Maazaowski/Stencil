"""Prompt-evaluation harness — offline unit tests (no AI).

Cover the scorers, the dataset reader, and report round-trip/compare. A stubbed
AI client exercises a runner without OpenAI; live runs are opt-in via the UI.
"""

from stencil.evals import report, scoring
from stencil.evals.runners import CaseResult
from stencil.output.mapper import ColumnDef

_COLUMNS = [
    ColumnDef(xlsx_header="EXT_SERVICEID", canonical_path="line_item.service_id", width=20),
    ColumnDef(xlsx_header="EXT_AMOUNT", canonical_path="line_item.amount", width=14),
]


def test_diff_metrics_perfect_and_partial():
    expected = [{"EXT_SERVICEID": "A1", "EXT_AMOUNT": "10.00"},
                {"EXT_SERVICEID": "B2", "EXT_AMOUNT": "20.00"}]
    perfect = [["A1", "10.00"], ["B2", "20.00"]]
    m = scoring.diff_metrics(expected, perfect, _COLUMNS)
    assert m["is_match"] is True and m["row_f1"] == 1.0

    missing_one = [["A1", "10.00"]]
    m2 = scoring.diff_metrics(expected, missing_one, _COLUMNS)
    assert m2["missing_rows"] == 1
    assert m2["row_recall"] == 0.5 and m2["row_precision"] == 1.0


def test_cell_diff_summary_collapses_a_systematic_column_defect():
    """A day/month-swapped date breaks every row; the summary must name the one
    column and the true row count, not emit one line per row."""
    columns = [
        ColumnDef(xlsx_header="EXT_SERVICEID", canonical_path="line_item.service_id", width=20),
        ColumnDef(xlsx_header="EXT_DATE", canonical_path="header.invoice_date", width=14),
    ]
    expected = [{"EXT_SERVICEID": f"S{i}", "EXT_DATE": "06/01/2026"} for i in range(300)]
    actual = [[f"S{i}", "01/06/2026"] for i in range(300)]

    m = scoring.diff_metrics(expected, actual, columns)

    assert m["matched_rows"] == 0  # whole-row key, so one bad column zeroes it
    summary = m["cell_diff_summary"]
    assert [s["column"] for s in summary] == ["EXT_DATE"]
    # Counted off the full diff, not the 200-entry truncation of cell_diffs.
    assert summary[0]["rows"] == 300
    assert summary[0]["expected"] == "06/01/2026"
    assert summary[0]["actual"] == "01/06/2026"


def test_cell_diff_summary_ranks_columns_by_rows_affected():
    columns = [
        ColumnDef(xlsx_header="EXT_SERVICEID", canonical_path="line_item.service_id", width=20),
        ColumnDef(xlsx_header="EXT_BILLINGREFERENCE", canonical_path="line_item.billing_reference", width=20),
    ]
    expected = [{"EXT_SERVICEID": f"S{i}", "EXT_BILLINGREFERENCE": "Ref"} for i in range(5)]
    # Only the last two rows also have a wrong reference.
    actual = [[f"X{i}", "Ref" if i < 3 else "Ref Ref"] for i in range(5)]

    summary = scoring.diff_metrics(expected, actual, columns)["cell_diff_summary"]

    assert [(s["column"], s["rows"]) for s in summary] == [
        ("EXT_SERVICEID", 5),
        ("EXT_BILLINGREFERENCE", 2),
    ]


def test_diff_metrics_normalizes_equivalent_dates_and_numbers():
    columns = [
        ColumnDef(xlsx_header="EXT_DATE", canonical_path="header.invoice_date", width=14),
        ColumnDef(xlsx_header="EXT_AMOUNT", canonical_path="line_item.amount", width=14),
        ColumnDef(xlsx_header="EXT_TAX", canonical_path="computed.line_tax", width=14),
    ]
    expected = [{"EXT_DATE": "03/01/2026", "EXT_AMOUNT": 65, "EXT_TAX": "8.630"}]
    actual = [["2026-03-01", "65.0", 8.63]]

    m = scoring.diff_metrics(expected, actual, columns)

    assert m["is_match"] is True
    assert m["row_f1"] == 1.0


def test_diff_metrics_normalizes_float_precision_noise():
    columns = [ColumnDef(xlsx_header="EXT_AMOUNT", canonical_path="line_item.amount", width=14)]
    expected = [{"EXT_AMOUNT": "1910.1799999999998"}]
    actual = [[1910.18]]

    m = scoring.diff_metrics(expected, actual, columns)

    assert m["is_match"] is True


def test_diff_metrics_reports_no_missing_or_extra_for_numeric_equivalents():
    columns = [
        ColumnDef(xlsx_header="EXT_SERVICEID", canonical_path="line_item.service_id", width=20),
        ColumnDef(xlsx_header="EXT_AMOUNT", canonical_path="line_item.amount", width=14),
        ColumnDef(xlsx_header="EXT_TAX", canonical_path="computed.line_tax", width=14),
    ]
    expected = [{"EXT_SERVICEID": "100-508-0018", "EXT_AMOUNT": "99.99", "EXT_TAX": "15"}]
    actual = [["100-508-0018", "99.990", "15.0"]]

    m = scoring.diff_metrics(expected, actual, columns)

    assert m["is_match"] is True
    assert m["missing_examples"] == []
    assert m["extra_examples"] == []


def test_diff_metrics_normalizes_text_wrapping_for_row_matching():
    columns = [
        ColumnDef(xlsx_header="EXT_SERVICEID", canonical_path="line_item.service_id", width=20),
        ColumnDef(xlsx_header="EXT_BILLINGREFERENCE", canonical_path="line_item.billing_reference", width=20),
        ColumnDef(xlsx_header="EXT_AMOUNT", canonical_path="line_item.amount", width=14),
    ]
    expected = [{
        "EXT_SERVICEID": "C35332-338",
        "EXT_BILLINGREFERENCE": (
            "Dedicated bandwidth enabling high speed application delivery within Metro "
            "markets Dublin to Dublin, Metro Wave, 10G\nEthernet "
        ),
        "EXT_AMOUNT": "850",
    }]
    actual = [[
        "C35332-338",
        "Dedicated bandwidth enabling high\nspeed application delivery within Metro\n"
        "markets\nDublin to Dublin, Metro Wave, 10G\nEthernet",
        850.0,
    ]]

    m = scoring.diff_metrics(expected, actual, columns)

    assert m["is_match"] is True
    assert m["row_f1"] == 1.0


def test_hallucination_flags_value_absent_from_source():
    rows = [["100-502-0019", "114.96"], ["999-999-9999", "50.00"]]
    source = "PDFSPLITSTART 100-502-0019 ... Total before taxes 114.96"
    flags = scoring.hallucination_flags(rows, _COLUMNS, source)
    flagged = {(f["value"]) for f in flags}
    # second row's id + amount are not in the source -> flagged; first row grounded.
    assert "999-999-9999" in flagged and "50.00" in flagged
    assert "100-502-0019" not in flagged and "114.96" not in flagged


def test_hallucination_ignores_computed_tax_and_delivered_dates():
    columns = [
        ColumnDef(xlsx_header="EXT_DATE", canonical_path="header.invoice_date", width=14),
        ColumnDef(xlsx_header="EXT_TAX", canonical_path="computed.line_tax", width=14),
        ColumnDef(xlsx_header="EXT_SERVICEID", canonical_path="line_item.service_id", width=20),
    ]
    rows = [["05/09/2026", "1395.86", "BFEC565162"]]
    source = "Billing Date May 9, 2026 Charges for Circuit #BFEC565162 ATI"

    assert scoring.hallucination_flags(rows, columns, source) == []


def test_hallucination_grounding_is_separator_insensitive():
    rows = [["100-502-0019", "1,455.10"]]
    source = "service 1005020019 amount 1455.10"  # no dashes / commas
    assert scoring.hallucination_flags(rows, _COLUMNS, source) == []


def test_hallucination_ignores_values_matching_expected_deliverable():
    columns = [
        ColumnDef(xlsx_header="EXT_SERVICEID", canonical_path="line_item.service_id", width=20),
        ColumnDef(xlsx_header="EXT_BILLINGREFERENCE", canonical_path="line_item.billing_reference", width=20),
    ]
    rows = [["416-219-3396", "5G Bus Intrnt Office Pro - 3yr"]]
    expected = [["416-219-3396", "5G Bus Intrnt Office Pro - 3yr"]]
    source = "Wireless 416-219-3396 5G Business Internet Office Pro 3 year plan"

    assert scoring.hallucination_flags(rows, columns, source, expected) == []


def test_classification_metrics_match():
    m = scoring.classification_metrics(
        {"supplier_name": "AT&T", "output_type": "standard"},
        {"supplier_name": "at&t", "output_type": "standard"},
    )
    assert m["is_match"] is True and m["supplier_match"] is True


def test_report_round_trip_and_compare(tmp_path, monkeypatch):
    monkeypatch.setattr(report.settings, "work_dir", tmp_path)

    def _result(case_id, f1):
        return CaseResult(
            case_id=case_id, layout_id="att.standard", call_type="extraction",
            metrics={
                "deliverable": {"row_f1": f1},
                "hallucinations": [],
                "est_cost_usd": 0.1,
                "duration_ms": 1234,
                "latency_ms": 987,
            },
        )

    a = report.write_run("runA", label="base", call_types=["extraction"], model="gpt-5.5",
                         results=[_result("att.standard/x", 0.5)])
    b = report.write_run("runB", label="cand", call_types=["extraction"], model="gpt-5.5",
                         results=[_result("att.standard/x", 0.9)])
    assert a["aggregate"]["mean_row_f1"] == 0.5
    assert b["aggregate"]["mean_row_f1"] == 0.9
    assert b["aggregate"]["total_duration_ms"] == 1234
    assert b["aggregate"]["total_ai_latency_ms"] == 987
    assert b["cases"][0]["duration_ms"] == 1234
    assert b["cases"][0]["ai_latency_ms"] == 987

    runs = {r["run_id"] for r in report.list_runs()}
    assert {"runA", "runB"} <= runs

    cmp = report.compare("runA", "runB")
    row = cmp["cases"][0]
    assert row["delta_row_f1"] == 0.4


def test_list_runs_includes_queued_status_only_run(tmp_path, monkeypatch):
    monkeypatch.setattr(report.settings, "work_dir", tmp_path)

    report.write_status("queuedRun", {
        "status": "queued",
        "created_at": "2026-07-03T10:00:00+00:00",
        "label": "current.v14",
        "call_types": ["extraction"],
        "run_kind": "current",
        "concurrency": 4,
        "selected_case_count": 38,
        "selected_layouts": ["att.standard", "rogers.standard"],
        "total_work_items": 38,
        "model": "gpt-5.5",
        "git_sha": "abc123",
        "done": 0,
        "total": 38,
    })

    runs = report.list_runs()

    assert runs[0]["run_id"] == "queuedRun"
    assert runs[0]["status"] == "queued"
    assert runs[0]["label"] == "current.v14"
    assert runs[0]["selected_case_count"] == 38
    assert runs[0]["selected_layouts"] == ["att.standard", "rogers.standard"]
    assert runs[0]["aggregate"]["cases"] == 0


def test_request_cancel_marks_status_cancelling(tmp_path, monkeypatch):
    monkeypatch.setattr(report.settings, "work_dir", tmp_path)

    report.write_status("runToCancel", {"status": "running", "done": 2, "total": 5})

    assert report.request_cancel("runToCancel") is True
    status = report.read_status("runToCancel")

    assert status["status"] == "cancelling"
    assert status["done"] == 2
    assert status["total"] == 5
    assert status["cancel_requested_at"]


def test_compare_handles_different_case_sets(tmp_path, monkeypatch):
    monkeypatch.setattr(report.settings, "work_dir", tmp_path)

    def _result(case_id, f1):
        return CaseResult(
            case_id=case_id,
            layout_id=case_id.split("/")[0],
            call_type="extraction",
            metrics={"deliverable": {"row_f1": f1}, "hallucinations": [], "est_cost_usd": 0.0},
        )

    report.write_run("runA", label="base", call_types=["extraction"], model="gpt-5.5",
                     results=[_result("att.standard/a", 0.5)])
    report.write_run("runB", label="current", call_types=["extraction"], model="gpt-5.5",
                     results=[_result("att.standard/a", 0.7), _result("zayo.standard/b", 1.0)])

    cmp = report.compare("runA", "runB")

    keys = {(row["case_id"], row["call_type"]) for row in cmp["cases"]}
    assert keys == {("att.standard/a", "extraction"), ("zayo.standard/b", "extraction")}
    assert any(row["delta_row_f1"] is None for row in cmp["cases"])


def test_baseline_pointer_persists_and_validates_completed_run(tmp_path, monkeypatch):
    monkeypatch.setattr(report.settings, "work_dir", tmp_path)

    result = CaseResult(
        case_id="att.standard/x",
        layout_id="att.standard",
        call_type="extraction",
        metrics={"deliverable": {"row_f1": 0.5}, "hallucinations": [], "est_cost_usd": 0.1},
    )
    report.write_run(
        "baseRun",
        label="baseline-v1",
        call_types=["extraction"],
        model="gpt-5.5",
        results=[result],
        run_kind="baseline",
    )

    baseline = report.accept_baseline("baseRun")

    assert baseline["run_id"] == "baseRun"
    assert baseline["accepted_from_label"] == "baseline-v1"
    assert report.read_baseline()["run_id"] == "baseRun"

    report.clear_baseline()
    assert report.read_baseline() is None

    try:
        report.accept_baseline("missing")
    except FileNotFoundError:
        pass
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("missing baseline run should fail")


def test_run_suite_records_kind_and_captured_baseline(tmp_path, monkeypatch):
    from stencil import runtime_settings
    from stencil.evals import dataset, runners

    monkeypatch.setattr(report.settings, "work_dir", tmp_path)
    monkeypatch.setattr(runtime_settings, "openai_model_extraction", lambda: "gpt-5.5")

    class _Case:
        case_id = "L/c1"
        layout_id = "L"

    monkeypatch.setattr(dataset, "discover_cases", lambda: [_Case()])
    monkeypatch.setattr(
        runners,
        "run_case",
        lambda case, call_type: CaseResult(
            case.case_id,
            case.layout_id,
            call_type,
            metrics={"deliverable": {"row_f1": 1.0}, "est_cost_usd": 0.0},
        ),
    )

    meta = report.run_suite(
        "currentRun",
        call_types=["extraction"],
        label="candidate",
        run_kind="current",
        baseline_run_id="baseRun",
    )

    assert meta["run_kind"] == "current"
    assert meta["baseline_run_id"] == "baseRun"
    assert report.read_run("currentRun")["baseline_run_id"] == "baseRun"


def test_render_report_html(tmp_path, monkeypatch):
    """The HTML report carries the shared prompt template, a layout group with its
    profile.json, and the case rows — enough to attribute a failure to prompt vs profile."""
    from stencil import runtime_settings
    from stencil.evals.report_html import render_report_html

    monkeypatch.setattr(report.settings, "work_dir", tmp_path)
    monkeypatch.setattr(
        runtime_settings, "runtime_value",
        lambda name: "compact_chunked" if name == "ai_extraction_mode" else None,
    )

    case = CaseResult(
        case_id="att.standard/ATT-0319605111", layout_id="att.standard", call_type="extraction",
        status="success",
        metrics={"deliverable": {"row_f1": 0.5}, "hallucinations": [],
                 "consistency": {"is_reconciled": True}, "est_cost_usd": 0.2},
        output_rows=[["A1", "10.00"]], expected_rows=[["A1", "10.00"]],
        columns=["EXT_SERVICEID", "EXT_AMOUNT"],
    )
    report.write_run("baseRpt", label="base", call_types=["extraction"], model="gpt-5.5", results=[case])
    report.write_run(
        "rptRun",
        label="current",
        call_types=["extraction"],
        model="gpt-5.5",
        results=[case],
        run_kind="current",
        baseline_run_id="baseRpt",
    )

    html_doc = render_report_html("rptRun")
    assert html_doc is not None
    assert "Baseline vs current" in html_doc
    assert "baseRpt" in html_doc and "rptRun" in html_doc
    assert "Prompt template (shared)" in html_doc          # the shared template section
    assert "{{PROFILE_HINTS}}" in html_doc                  # template shows variable slots
    assert "att.standard" in html_doc and "ATT-0319605111" in html_doc
    assert "EXT_SERVICEID" in html_doc                      # expected/actual rows rendered
    assert "profile.json" in html_doc                       # att.standard profile embedded
    assert render_report_html("does-not-exist") is None


def test_run_suite_concurrency_runs_every_case_once(tmp_path, monkeypatch):
    import threading

    from stencil import runtime_settings
    from stencil.evals import dataset, runners

    monkeypatch.setattr(report.settings, "work_dir", tmp_path)
    monkeypatch.setattr(runtime_settings, "openai_model_extraction", lambda: "gpt-5.5")

    class _Case:
        def __init__(self, cid):
            self.case_id = cid
            self.layout_id = "L"

    cases = [_Case(f"L/c{i}") for i in range(8)]
    monkeypatch.setattr(dataset, "discover_cases", lambda: cases)

    seen: list[str] = []
    lock = threading.Lock()

    def fake_run_case(case, call_type):
        with lock:
            seen.append(case.case_id)
        return CaseResult(case.case_id, case.layout_id, call_type,
                          metrics={"deliverable": {"row_f1": 1.0}, "est_cost_usd": 0.0})

    monkeypatch.setattr(runners, "run_case", fake_run_case)

    meta = report.run_suite("concRun", call_types=["extraction"], label="x", concurrency=4)
    assert meta["aggregate"]["cases"] == 8
    assert sorted(seen) == sorted(c.case_id for c in cases)        # every case ran exactly once
    assert [c["case_id"] for c in meta["cases"]] == sorted(c.case_id for c in cases)  # deterministic index
    status = report.read_status("concRun")
    assert status["status"] == "done" and status["total"] == 8


def test_endpoint_forbidden_when_debug_off(monkeypatch):
    """Evals expose prompts/outputs — must 403 outside debug mode (incl. the report)."""
    from fastapi.testclient import TestClient

    from stencil.api import evals as api_evals
    from stencil.main import app

    monkeypatch.setattr(
        api_evals.runtime_settings, "runtime_value",
        lambda name: False if name == "debug" else None,
    )
    client = TestClient(app)
    assert client.get("/api/v1/debug/evals/runs").status_code == 403
    assert client.get("/api/v1/debug/evals/runs/x/report.html").status_code == 403


def test_baseline_api_and_current_start_capture(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from stencil.api import evals as api_evals
    from stencil.main import app
    from stencil.tasks import worker

    monkeypatch.setattr(report.settings, "work_dir", tmp_path)
    monkeypatch.setattr(api_evals.settings, "work_dir", tmp_path)
    monkeypatch.setattr(
        api_evals.runtime_settings,
        "runtime_value",
        lambda name: True if name == "debug" else None,
    )

    result = CaseResult(
        case_id="att.standard/x",
        layout_id="att.standard",
        call_type="extraction",
        metrics={"deliverable": {"row_f1": 0.5}, "hallucinations": [], "est_cost_usd": 0.1},
    )
    report.write_run("baseRun", label="accepted", call_types=["extraction"], model="gpt-5.5", results=[result])

    client = TestClient(app)
    missing = client.put("/api/v1/debug/evals/baseline", json={"run_id": "missing"})
    assert missing.status_code == 404

    accepted = client.put("/api/v1/debug/evals/baseline", json={"run_id": "baseRun"})
    assert accepted.status_code == 200
    assert accepted.json()["baseline"]["run_id"] == "baseRun"
    assert client.get("/api/v1/debug/evals/baseline").json()["baseline"]["run_id"] == "baseRun"

    queued = []
    monkeypatch.setattr(worker.eval_run_task, "delay", lambda *args: queued.append(args))

    started = client.post(
        "/api/v1/debug/evals/runs",
        json={"call_types": ["extraction"], "label": "candidate", "run_kind": "current"},
    )

    assert started.status_code == 200, started.text
    assert queued
    assert queued[0][-2:] == ("current", "baseRun")


def test_compare_classifies_and_sorts_regressions_first(tmp_path, monkeypatch):
    monkeypatch.setattr(report.settings, "work_dir", tmp_path)

    def _result(case_id, f1):
        return CaseResult(
            case_id=case_id, layout_id=case_id.split("/")[0], call_type="extraction",
            metrics={
                "deliverable": {"row_f1": f1},
                "hallucinations": [],
                "consistency": {"is_reconciled": True},
                "est_cost_usd": 0.1,
                "duration_ms": 1200,
                "latency_ms": 900,
            },
        )

    report.write_run("base", label="base", call_types=["extraction"], model="m",
                     results=[_result("a/up", 0.5), _result("a/down", 0.9),
                              _result("a/same", 0.7), _result("a/gone", 1.0)])
    report.write_run("cur", label="cur", call_types=["extraction"], model="m",
                     results=[_result("a/up", 0.8), _result("a/down", 0.4),
                              _result("a/same", 0.7), _result("a/added", 1.0)])

    cmp = report.compare("base", "cur")
    by_case = {r["case_id"]: r["status"] for r in cmp["cases"]}
    assert by_case == {"a/down": "regressed", "a/gone": "missing",
                       "a/added": "new", "a/up": "improved", "a/same": "unchanged"}
    # Regressions surface first.
    assert cmp["cases"][0]["case_id"] == "a/down"
    assert cmp["cases"][0]["a_duration_ms"] == 1200
    assert cmp["cases"][0]["b_ai_latency_ms"] == 900
    assert cmp["cases"][0]["a_is_reconciled"] is True
    assert cmp["summary"]["regressed"] == 1
    assert cmp["summary"]["improved"] == 1


def test_run_suite_streams_partial_cases_while_running(tmp_path, monkeypatch):
    """Each finished case is persisted immediately so a live poll (read_run) can
    show it before the run completes and run.json is written."""
    from stencil import runtime_settings
    from stencil.evals import dataset, runners

    monkeypatch.setattr(report.settings, "work_dir", tmp_path)
    monkeypatch.setattr(runtime_settings, "openai_model_extraction", lambda: "gpt-5.5")

    class _Case:
        def __init__(self, cid):
            self.case_id = cid
            self.layout_id = "L"

    cases = [_Case("L/c0"), _Case("L/c1")]
    monkeypatch.setattr(dataset, "discover_cases", lambda: cases)

    snapshots = []

    def fake_run_case(case, call_type):
        # What a poll would see at the moment this case starts (before it's persisted).
        partial = report.read_run("streamRun")
        snapshots.append((partial and partial.get("status"), len((partial or {}).get("cases") or [])))
        return CaseResult(case.case_id, case.layout_id, call_type,
                          metrics={"deliverable": {"row_f1": 1.0}, "est_cost_usd": 0.0})

    monkeypatch.setattr(runners, "run_case", fake_run_case)

    meta = report.run_suite("streamRun", call_types=["extraction"], label="x", concurrency=1)

    # First case sees nothing persisted; by the second, the first has streamed while "running".
    assert snapshots[0] == ("running", 0)
    assert snapshots[1] == ("running", 1)
    # run.json wins once done: authoritative full index, both cases.
    assert [c["case_id"] for c in meta["cases"]] == ["L/c0", "L/c1"]
    assert report.read_status("streamRun")["status"] == "done"


def test_run_suite_cancel_skips_unstarted_cases(tmp_path, monkeypatch):
    monkeypatch.setattr(report.settings, "work_dir", tmp_path)
    from pathlib import Path as PathT

    from stencil.evals import runners
    from stencil.evals.dataset import EvalCase

    cases = [
        EvalCase(case_id=f"l/x{i}", layout_id="l", pdf_path=PathT(f"x{i}.pdf"),
                 profile_path=PathT("p.json"), expected_xlsx_path=PathT(f"x{i}.xlsx"))
        for i in range(3)
    ]
    monkeypatch.setattr("stencil.evals.dataset.discover_cases", lambda *a, **k: cases)

    calls = []

    def fake_run_case(case, call_type):
        calls.append(case.case_id)
        # Cancel after the FIRST case completes: the rest must be skipped.
        report.request_cancel("cx")
        return CaseResult(case_id=case.case_id, layout_id="l", call_type=call_type,
                          metrics={"deliverable": {"row_f1": 1.0}, "hallucinations": []})

    monkeypatch.setattr(runners, "run_case", fake_run_case)
    report.run_dir("cx").mkdir(parents=True, exist_ok=True)

    meta = report.run_suite("cx", call_types=["extraction"], label="t", concurrency=1)

    assert len(calls) == 1  # only the first case ran
    assert len(meta["cases"]) == 1  # partial results kept
    assert report.read_status("cx")["status"] == "cancelled"
