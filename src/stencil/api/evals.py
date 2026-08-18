"""Dev-only prompt-evaluation API.

Starts eval runs (live AI in the worker) and serves the scored artifacts for the
in-app Evals page. Every endpoint is gated on the live ``debug`` runtime setting
(``ST_DEBUG``) — 403 in production.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from stencil import runtime_settings
from stencil.config import settings
from stencil.evals import report
from stencil.evals.dataset import discover_cases
from stencil.evals.runners import CALL_TYPES

logger = structlog.get_logger()

router = APIRouter(prefix="/debug/evals", tags=["debug"])


class StartRunRequest(BaseModel):
    call_types: list[str] = Field(default_factory=lambda: ["extraction"])
    label: str = ""
    case_ids: list[str] | None = None
    concurrency: int | None = None
    run_kind: Literal["baseline", "current"] = "current"


class BaselineRequest(BaseModel):
    run_id: str


def _require_debug() -> None:
    if not bool(runtime_settings.runtime_value("debug")):
        raise HTTPException(status_code=403, detail="Evals are only available in debug mode")


@router.get("/cases")
def list_cases():
    _require_debug()
    cases = discover_cases()
    return {
        "call_types": CALL_TYPES,
        "cases": [
            {"case_id": c.case_id, "layout_id": c.layout_id, "has_expected": c.has_expected}
            for c in cases
        ],
    }


@router.post("/runs")
def start_run(body: StartRunRequest):
    _require_debug()
    from stencil.tasks.worker import eval_run_task

    invalid = [c for c in body.call_types if c not in CALL_TYPES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown call types: {invalid}")
    if not body.call_types:
        raise HTTPException(status_code=400, detail="Pick at least one call type")

    concurrency = body.concurrency if body.concurrency and body.concurrency > 0 else settings.eval_run_concurrency
    active_baseline = report.read_baseline()
    baseline_run_id = (
        active_baseline.get("run_id")
        if body.run_kind == "current" and isinstance(active_baseline, dict)
        else None
    )
    selected_cases = discover_cases()
    if body.case_ids:
        wanted = set(body.case_ids)
        selected_cases = [c for c in selected_cases if c.case_id in wanted]
    selected_layouts = sorted({c.layout_id for c in selected_cases})
    total_work_items = len(selected_cases) * len(body.call_types)
    run_id = report.new_run_id()
    report.write_status(run_id, {
        "status": "queued",
        "done": 0,
        "total": total_work_items,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "label": body.label,
        "call_types": body.call_types,
        "run_kind": body.run_kind,
        "baseline_run_id": baseline_run_id,
        "concurrency": concurrency,
        "selected_case_count": len(selected_cases),
        "selected_layouts": selected_layouts,
        "total_work_items": total_work_items,
        "model": runtime_settings.openai_model_extraction(),
        "git_sha": report.git_sha(),
    })
    eval_run_task.delay(
        run_id,
        body.call_types,
        body.label,
        body.case_ids,
        concurrency,
        body.run_kind,
        baseline_run_id,
    )
    logger.info(
        "eval.run.queued",
        run_id=run_id,
        call_types=body.call_types,
        concurrency=concurrency,
        run_kind=body.run_kind,
        baseline_run_id=baseline_run_id,
    )
    return {"run_id": run_id, "status": "queued"}


@router.get("/baseline")
def get_baseline():
    _require_debug()
    return {"baseline": report.read_baseline()}


@router.put("/baseline")
def set_baseline(body: BaselineRequest):
    _require_debug()
    try:
        baseline = report.accept_baseline(body.run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"baseline": baseline}


@router.delete("/baseline")
def delete_baseline():
    _require_debug()
    report.clear_baseline()
    return {"baseline": None}


@router.get("/runs")
def list_runs():
    _require_debug()
    return {"runs": report.list_runs()}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    _require_debug()
    meta = report.read_run(run_id)
    status = report.read_status(run_id)
    if meta is None and status is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run": meta, "status": status}


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    """Cooperatively stop a running suite: unstarted cases are skipped,
    in-flight cases finish, partial results are kept."""
    _require_debug()
    if not report.request_cancel(run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    return {"status": "cancelling"}


@router.get("/runs/{run_id}/cases/{case_file}")
def get_case(run_id: str, case_file: str):
    _require_debug()
    case = report.read_case(run_id, case_file)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.get("/runs/{run_id}/report.html", response_class=HTMLResponse)
def get_run_report(run_id: str):
    """Self-contained HTML report for a run (cases grouped by profile.json + the
    shared prompt template), so a failure can be attributed to prompt vs profile."""
    _require_debug()
    from stencil.evals.report_html import render_report_html

    html_doc = render_report_html(run_id)
    if html_doc is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return HTMLResponse(content=html_doc)


@router.get("/compare")
def compare_runs(a: str, b: str):
    _require_debug()
    result = report.compare(a, b)
    if result is None:
        raise HTTPException(status_code=404, detail="One or both runs not found")
    return result
