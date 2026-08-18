"""Eval run artifacts: write/read/aggregate/compare.

Each run is a directory ``work_dir/evals/<run_id>/`` with ``run.json`` (meta +
aggregate + lightweight per-case index) and one ``<case>.json`` per case holding
the full detail (rows, diffs, hallucinations, prompt refs). File-based like
``ai_debug`` — no DB migration; runs are self-contained and comparable.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from stencil.config import settings
from stencil.evals.runners import CaseResult


def evals_root() -> Path:
    return settings.work_dir / "evals"


def run_dir(run_id: str) -> Path:
    return evals_root() / run_id


def baseline_path() -> Path:
    return evals_root() / "baseline.json"


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:6]


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _empty_aggregate() -> dict:
    return {
        "cases": 0,
        "by_status": {},
        "mean_row_f1": None,
        "scored_cases": 0,
        "total_hallucinations": 0,
        "reconciled_pct": None,
        "classification_pct": None,
        "total_cost_usd": 0.0,
    }


def _case_file(result: CaseResult) -> str:
    return f"{result.case_id.replace('/', '__')}__{result.call_type}.json"


def _case_index_entry(r: CaseResult) -> dict:
    """The lightweight per-case row shown in the run index (and streamed live)."""
    m = r.metrics or {}
    deliverable = m.get("deliverable") or {}
    classif = m.get("classification") or {}
    return {
        "file": _case_file(r),
        "case_id": r.case_id,
        "call_type": r.call_type,
        "status": r.status,
        "error": r.error,
        "row_f1": deliverable.get("row_f1"),
        "hallucinations": len(m.get("hallucinations") or []),
        "is_reconciled": (m.get("consistency") or {}).get("is_reconciled"),
        "classification_match": classif.get("is_match"),
        "est_cost_usd": m.get("est_cost_usd"),
        "duration_ms": m.get("duration_ms"),
        "ai_latency_ms": m.get("latency_ms"),
    }


def _write_case_file(directory: Path, r: CaseResult) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / _case_file(r)).write_text(
        json.dumps(r.as_dict(), indent=2, default=str), encoding="utf-8"
    )


def aggregate(results: list[CaseResult]) -> dict:
    by_status: dict[str, int] = {}
    f1s: list[float] = []
    reconciled = recon_total = 0
    halluc = 0
    classif_ok = classif_total = 0
    cost = 0.0
    duration_ms = 0
    latency_ms = 0
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1
        m = r.metrics or {}
        cost += float(m.get("est_cost_usd") or 0.0)
        duration_ms += int(m.get("duration_ms") or 0)
        latency_ms += int(m.get("latency_ms") or 0)
        deliverable = m.get("deliverable")
        if deliverable:
            f1s.append(float(deliverable.get("row_f1") or 0.0))
        halluc += len(m.get("hallucinations") or [])
        consistency = m.get("consistency")
        if consistency:
            recon_total += 1
            reconciled += 1 if consistency.get("is_reconciled") else 0
        classif = m.get("classification")
        if classif:
            classif_total += 1
            classif_ok += 1 if classif.get("is_match") else 0
    return {
        "cases": len(results),
        "by_status": by_status,
        "mean_row_f1": round(sum(f1s) / len(f1s), 4) if f1s else None,
        "scored_cases": len(f1s),
        "total_hallucinations": halluc,
        "reconciled_pct": round(100 * reconciled / recon_total, 1) if recon_total else None,
        "classification_pct": round(100 * classif_ok / classif_total, 1) if classif_total else None,
        "total_cost_usd": round(cost, 4),
        "total_duration_ms": duration_ms,
        "total_ai_latency_ms": latency_ms,
    }


def write_run(
    run_id: str,
    *,
    label: str,
    call_types: list[str],
    model: str,
    results: list[CaseResult],
    run_kind: str = "current",
    baseline_run_id: str | None = None,
    concurrency: int | None = None,
    selected_case_count: int | None = None,
    selected_layouts: list[str] | None = None,
    total_work_items: int | None = None,
) -> dict:
    directory = run_dir(run_id)
    directory.mkdir(parents=True, exist_ok=True)
    index = []
    for r in results:
        _write_case_file(directory, r)
        index.append(_case_index_entry(r))
    meta = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "label": label,
        "call_types": call_types,
        "model": model,
        "git_sha": git_sha(),
        "run_kind": run_kind,
        "baseline_run_id": baseline_run_id,
        "concurrency": concurrency,
        "selected_case_count": selected_case_count,
        "selected_layouts": selected_layouts or [],
        "total_work_items": total_work_items,
        "aggregate": aggregate(results),
        "cases": index,
    }
    (directory / "run.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    _prune_runs()
    return meta


def write_status(run_id: str, status: dict) -> None:
    directory = run_dir(run_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "status.json"
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing.update(status)
    path.write_text(json.dumps(existing, default=str), encoding="utf-8")


def request_cancel(run_id: str) -> bool:
    """Ask a running suite to stop after in-flight cases. Returns False if unknown."""
    directory = run_dir(run_id)
    if not directory.is_dir():
        return False
    (directory / "cancel.json").write_text("{}", encoding="utf-8")
    status = read_status(run_id) or {}
    if status.get("status") not in {"done", "cancelled", "error"}:
        write_status(run_id, {
            "status": "cancelling",
            "cancel_requested_at": datetime.now(UTC).isoformat(timespec="seconds"),
        })
    return True


def cancel_requested(run_id: str) -> bool:
    return (run_dir(run_id) / "cancel.json").exists()


def read_status(run_id: str) -> dict | None:
    path = run_dir(run_id) / "status.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def read_baseline() -> dict | None:
    path = baseline_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def accept_baseline(run_id: str) -> dict:
    meta = read_run(run_id)
    if meta is None:
        raise FileNotFoundError(f"Eval run not found: {run_id}")

    baseline = {
        "run_id": run_id,
        "accepted_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "accepted_from_label": meta.get("label") or "",
    }
    path = baseline_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline, indent=2, default=str), encoding="utf-8")
    return baseline


def clear_baseline() -> None:
    path = baseline_path()
    if path.exists():
        path.unlink()


def run_suite(
    run_id: str,
    *,
    call_types: list[str],
    label: str,
    case_ids: list[str] | None = None,
    concurrency: int = 1,
    run_kind: str = "current",
    baseline_run_id: str | None = None,
) -> dict:
    """Run the selected call types over the labeled cases (live AI) and persist results.

    Cases run in a thread pool of ``concurrency`` workers (each ``run_case`` opens its
    own short-lived DB sessions and its own ``ai_debug`` capture context, so workers do
    not share mutable state). Progress is reported under a lock; the persisted index is
    sorted for a deterministic order regardless of completion timing.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from stencil import runtime_settings
    from stencil.evals.dataset import discover_cases
    from stencil.evals.runners import run_case

    cases = discover_cases()
    if case_ids:
        wanted = set(case_ids)
        cases = [c for c in cases if c.case_id in wanted]

    pairs = [(case, call_type) for case in cases for call_type in call_types]
    total = len(pairs)
    selected_layouts = sorted({c.layout_id for c in cases})
    selected_case_count = len(cases)
    workers = max(1, int(concurrency or 1))
    write_status(run_id, {
        "status": "running",
        "done": 0,
        "total": total,
        "label": label,
        "call_types": call_types,
        "run_kind": run_kind,
        "baseline_run_id": baseline_run_id,
        "concurrency": workers,
        "selected_case_count": selected_case_count,
        "selected_layouts": selected_layouts,
        "total_work_items": total,
        "model": runtime_settings.openai_model_extraction(),
        "git_sha": git_sha(),
    })

    lock = threading.Lock()
    done = 0
    partial: list[dict] = []

    def _run(pair) -> CaseResult | None:
        nonlocal done
        # Cooperative cancel: pairs that have not started yet are skipped;
        # in-flight cases finish and their results are kept.
        if cancel_requested(run_id):
            return None
        case, call_type = pair
        result = run_case(case, call_type)
        with lock:
            done += 1
            # Persist this case immediately so the run detail can stream it live
            # (the poll reads partial_cases until the final run.json is written).
            _write_case_file(run_dir(run_id), result)
            partial.append(_case_index_entry(result))
            status_name = "cancelling" if cancel_requested(run_id) else "running"
            write_status(run_id, {
                "status": status_name, "done": done, "total": total,
                "partial_cases": list(partial),
            })
        return result

    if workers == 1 or total <= 1:
        results = [_run(pair) for pair in pairs]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_run, pairs))

    results = [r for r in results if r is not None]
    results.sort(key=lambda r: (r.case_id, r.call_type))
    model = runtime_settings.openai_model_extraction()
    meta = write_run(
        run_id,
        label=label,
        call_types=call_types,
        model=model,
        results=results,
        run_kind=run_kind,
        baseline_run_id=baseline_run_id,
        concurrency=workers,
        selected_case_count=selected_case_count,
        selected_layouts=selected_layouts,
        total_work_items=total,
    )
    final = "cancelled" if cancel_requested(run_id) else "done"
    write_status(run_id, {"status": final, "done": len(results), "total": total})
    return meta


def list_runs() -> list[dict]:
    root = evals_root()
    if not root.is_dir():
        return []
    runs = []
    for d in sorted(root.iterdir(), reverse=True):
        run_json = d / "run.json"
        status_json = d / "status.json"
        if run_json.exists():
            try:
                meta = json.loads(run_json.read_text(encoding="utf-8"))
            except Exception:
                continue
            keys = (
                "run_id", "created_at", "label", "call_types", "model", "git_sha",
                "run_kind", "baseline_run_id", "concurrency", "selected_case_count",
                "selected_layouts", "total_work_items", "aggregate",
            )
            summary = {k: meta[k] for k in keys if k in meta}
            if status_json.exists():
                try:
                    status = json.loads(status_json.read_text(encoding="utf-8"))
                    summary["status"] = status.get("status")
                    summary["done"] = status.get("done")
                    summary["total"] = status.get("total")
                except Exception:
                    pass
            runs.append(summary)
        elif status_json.exists():
            try:
                status = json.loads(status_json.read_text(encoding="utf-8"))
            except Exception:
                continue
            runs.append(_summary_from_status(d.name, status))
    return runs


def read_run(run_id: str) -> dict | None:
    run_json = run_dir(run_id) / "run.json"
    if not run_json.exists():
        status = read_status(run_id)
        if not status:
            return None
        return _summary_from_status(run_id, status) | {"cases": status.get("partial_cases") or []}
    return json.loads(run_json.read_text(encoding="utf-8"))


def _summary_from_status(run_id: str, status: dict) -> dict:
    return {
        "run_id": run_id,
        "created_at": status.get("created_at") or run_id.split("_", 1)[0],
        "label": status.get("label") or "",
        "call_types": status.get("call_types") or [],
        "model": status.get("model") or "unknown",
        "git_sha": status.get("git_sha") or "unknown",
        "run_kind": status.get("run_kind"),
        "baseline_run_id": status.get("baseline_run_id"),
        "concurrency": status.get("concurrency"),
        "selected_case_count": status.get("selected_case_count"),
        "selected_layouts": status.get("selected_layouts") or [],
        "total_work_items": status.get("total_work_items") or status.get("total"),
        "status": status.get("status"),
        "done": status.get("done"),
        "total": status.get("total"),
        "aggregate": _empty_aggregate(),
    }


def read_case(run_id: str, case_file: str) -> dict | None:
    path = (run_dir(run_id) / case_file).resolve()
    base = run_dir(run_id).resolve()
    if not str(path).startswith(str(base)) or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def compare(run_a: str, run_b: str) -> dict | None:
    a, b = read_run(run_a), read_run(run_b)
    if a is None or b is None:
        return None
    a_idx = {(c["case_id"], c["call_type"]): c for c in a.get("cases", [])}
    b_idx = {(c["case_id"], c["call_type"]): c for c in b.get("cases", [])}
    rows = []
    for key in sorted(set(a_idx) | set(b_idx)):
        ca, cb = a_idx.get(key), b_idx.get(key)
        fa = (ca or {}).get("row_f1")
        fb = (cb or {}).get("row_f1")
        delta = round(fb - fa, 4) if (fa is not None and fb is not None) else None
        if ca is None:
            status = "new"
        elif cb is None:
            status = "missing"
        elif delta is None:
            status = "unscored"
        elif delta < 0:
            status = "regressed"
        elif delta > 0:
            status = "improved"
        else:
            status = "unchanged"
        rows.append({
            "case_id": key[0],
            "call_type": key[1],
            "status": status,
            "a_status": (ca or {}).get("status"),
            "b_status": (cb or {}).get("status"),
            "a_row_f1": fa,
            "b_row_f1": fb,
            "delta_row_f1": delta,
            "a_hallucinations": (ca or {}).get("hallucinations"),
            "b_hallucinations": (cb or {}).get("hallucinations"),
            "a_is_reconciled": (ca or {}).get("is_reconciled"),
            "b_is_reconciled": (cb or {}).get("is_reconciled"),
            "a_est_cost_usd": (ca or {}).get("est_cost_usd"),
            "b_est_cost_usd": (cb or {}).get("est_cost_usd"),
            "a_duration_ms": (ca or {}).get("duration_ms"),
            "b_duration_ms": (cb or {}).get("duration_ms"),
            "a_ai_latency_ms": (ca or {}).get("ai_latency_ms"),
            "b_ai_latency_ms": (cb or {}).get("ai_latency_ms"),
        })
    # Regressions surface first, worst delta first; then the rest, stable order.
    order = {"regressed": 0, "missing": 1, "new": 2, "unscored": 3, "improved": 4, "unchanged": 5}
    rows.sort(key=lambda r: (order[r["status"]], r["delta_row_f1"] or 0, r["case_id"]))
    summary = {s: sum(1 for r in rows if r["status"] == s) for s in order}
    return {
        "a": a["aggregate"], "b": b["aggregate"], "a_id": run_a, "b_id": run_b,
        "a_label": a.get("label"), "b_label": b.get("label"),
        "summary": summary, "cases": rows,
    }


def _prune_runs() -> None:
    try:
        cap = int(settings.eval_max_runs)
    except Exception:
        cap = 50
    if cap <= 0:
        return
    root = evals_root()
    dirs = sorted([d for d in root.iterdir() if (d / "run.json").exists()])
    import shutil

    for stale in dirs[:-cap]:
        shutil.rmtree(stale, ignore_errors=True)
