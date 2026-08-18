"""Self-contained HTML report for one eval run.

Renders a single static page (inline CSS, no external assets) structured so a
reader can attribute a failure to the **prompt** or the **profile.json**:

- one shared **PROMPT TEMPLATE** (the extraction prompt as a template with explicit
  variable slots — same text the code uses, via
  ``compact_chunked.extraction_prompt_template``); then
- cases grouped **by layout (= one profile.json)**, each group showing that
  profile.json and every case's score + expected-vs-actual rows + a representative
  rendered prompt.

A failure that repeats across every layout points at the prompt; one isolated to a
single layout points at that profile.json.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from stencil.ai_debug import ai_debug_dir
from stencil.evals import report
from stencil.evals.dataset import discover_cases

_CSS = """
* { box-sizing: border-box; }
html { color-scheme: light; }
body { font: 13px/1.55 ui-sans-serif, system-ui, -apple-system, sans-serif;
       margin: 0; padding: 24px; background: #ffffff; color: #0f172a; }
h1 { font-size: 22px; margin: 0 0 4px; color: #0f172a; }
h2 { font-size: 17px; margin: 30px 0 10px; color: #0f172a; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px; }
h3 { font-size: 13px; margin: 14px 0 6px; color: #0f172a; }
.muted { color: #64748b; }
.meta { color: #64748b; margin: 0 0 18px; font-size: 12px; }
section.card { border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px 16px; margin: 14px 0;
               background: #ffffff; box-shadow: 0 1px 2px rgba(0,0,0,.05); }
table { border-collapse: collapse; width: 100%; font: 11px/1.4 ui-monospace, monospace; }
th, td { border: 1px solid #e5e7eb; padding: 3px 6px; text-align: left; vertical-align: top; color: #0f172a; }
th { background: #f1f5f9; position: sticky; top: 0; }
tbody tr:nth-child(even) { background: #f8fafc; }
.scroll { max-height: 360px; overflow: auto; border: 1px solid #e2e8f0; border-radius: 6px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
pre { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px; overflow: auto;
      white-space: pre-wrap; word-break: break-word; font: 11px/1.45 ui-monospace, monospace;
      max-height: 420px; color: #0f172a; }
.badge { display: inline-block; border-radius: 4px; padding: 1px 7px; font-size: 11px; margin: 0 6px 4px 0;
         background: #f1f5f9; color: #334155; }
.ok { background: #dcfce7; color: #166534; }
.bad { background: #fee2e2; color: #991b1b; }
.warn { background: #fef3c7; color: #92400e; }
.kpis { margin: 4px 0 8px; }
.kpis span { display: inline-block; margin-right: 18px; }
details { margin: 8px 0; }
summary { cursor: pointer; font-weight: 600; color: #0f172a; }
.halluc { background: #fffbeb; border: 1px solid #fde68a; border-radius: 6px; padding: 8px 10px;
          color: #92400e; word-break: break-word; }
.halluc code { background: #fef3c7; border-radius: 3px; padding: 0 4px; margin: 0 5px 4px 0; display: inline-block; }
"""


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _rows_table(columns: list[str], rows: list[list[Any]], title: str) -> str:
    head = "".join(f"<th>{_esc(c)}</th>" for c in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_esc(cell)}</td>" for cell in row) + "</tr>" for row in rows
    )
    return (
        f'<h3>{_esc(title)} ({len(rows)})</h3>'
        f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
    )


def _profile_paths() -> dict[str, Path]:
    """layout_id -> its profile.json path (one profile per layout folder)."""
    out: dict[str, Path] = {}
    for case in discover_cases():
        out.setdefault(case.layout_id, case.profile_path)
    return out


def _representative_prompt(prompt_files: list[str]) -> str:
    """Raw markdown dump (prompt + schema + response) of the first captured call."""
    directory = ai_debug_dir()
    for name in prompt_files:
        path = directory / name
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


def _prompt_template_section() -> str:
    from stencil.extraction.compact_chunked import extraction_prompt_template

    tpl = extraction_prompt_template()
    variables = "".join(
        f"<tr><td><code>{{{{{_esc(k)}}}}}</code></td><td>{_esc(v)}</td></tr>"
        for k, v in tpl.get("variables", {}).items()
    )
    calls = "".join(
        f"<h3>user prompt — {_esc(call['call_type'])}</h3><pre>{_esc(call['user_template'])}</pre>"
        for call in tpl.get("calls", [])
    )
    note = f'<p class="warn badge">{_esc(tpl["note"])}</p>' if tpl.get("note") else ""
    return (
        '<section class="card"><h2>Prompt template (shared)</h2>'
        f'<p class="muted">extraction mode: <code>{_esc(tpl.get("extraction_mode"))}</code> · '
        "the same prompt runs for every case; only the variables below change per invoice.</p>"
        f"{note}"
        f"<h3>system prompt</h3><pre>{_esc(tpl.get('system'))}</pre>"
        f"{calls}"
        "<h3>variables (filled in per case)</h3>"
        f"<table><thead><tr><th>placeholder</th><th>source</th></tr></thead><tbody>{variables}</tbody></table>"
        "</section>"
    )


def _case_block(case: dict[str, Any]) -> str:
    metrics = case.get("metrics") or {}
    deliverable = metrics.get("deliverable") or {}
    halluc = metrics.get("hallucinations") or []
    consistency = metrics.get("consistency") or {}
    f1 = deliverable.get("row_f1")
    reconciled = consistency.get("is_reconciled")
    recon_symbol = "✓" if reconciled else ("✗" if reconciled is not None else "—")
    status_class = "ok" if case.get("status") == "success" else "bad"
    badges = [
        f'<span class="badge {status_class}">{_esc(case.get("status"))}</span>',
        f'<span class="badge">F1 {f1 if f1 is not None else "—"}</span>',
        f'<span class="badge {"warn" if halluc else "ok"}">halluc {len(halluc)}</span>',
        f'<span class="badge {"ok" if reconciled else "bad"}">recon {recon_symbol}</span>',
        f'<span class="badge">duration {_format_ms(metrics.get("duration_ms"))}</span>',
        f'<span class="badge">AI {_format_ms(metrics.get("latency_ms"))}</span>',
    ]
    error = f'<p class="bad">{_esc(case.get("error"))}</p>' if case.get("error") else ""
    # Dedupe repeated (column, value) flags — the same date/value flagged on every row is
    # noise; show distinct values, capped, so the block stays readable.
    distinct: list[tuple[Any, Any]] = []
    for h in halluc:
        key = (h.get("column"), h.get("value"))
        if key not in distinct:
            distinct.append(key)
    cap = 25
    chips = "".join(f"<code>{_esc(col)}={_esc(val)}</code>" for col, val in distinct[:cap])
    more = f' <span class="muted">+{len(distinct) - cap} more distinct</span>' if len(distinct) > cap else ""
    halluc_line = (
        f'<div class="halluc"><b>Possible hallucinations</b> — {len(halluc)} flags, '
        f"{len(distinct)} distinct (value not found in source text): {chips}{more}</div>"
        if halluc
        else ""
    )
    columns = case.get("columns") or []
    tables = (
        '<div class="grid">'
        + _rows_table(columns, case.get("expected_rows") or [], "Expected")
        + _rows_table(columns, case.get("output_rows") or [], "Actual")
        + "</div>"
    )
    rendered = _representative_prompt(case.get("prompt_files") or [])
    prompt = (
        f"<details><summary>Rendered prompt (this case)</summary><pre>{_esc(rendered)}</pre></details>"
        if rendered
        else '<p class="muted">No captured prompt on disk (may have been pruned).</p>'
    )
    return (
        f'<section class="card"><h3>{_esc(case.get("case_id"))} · {_esc(case.get("call_type"))}</h3>'
        f'<p>{"".join(badges)}</p>{error}{halluc_line}{tables}{prompt}</section>'
    )


def _format_ms(value: object) -> str:
    try:
        ms = int(value or 0)
    except Exception:
        return "—"
    if ms <= 0:
        return "—"
    if ms < 1000:
        return f"{ms}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    return f"{minutes}m {round(seconds % 60)}s"


def _bool_symbol(value: object) -> str:
    if value is True:
        return "✓"
    if value is False:
        return "✗"
    return "—"


def _comparison_section(meta: dict[str, Any]) -> str:
    baseline_id = meta.get("baseline_run_id")
    run_id = meta.get("run_id")
    if not baseline_id or not run_id:
        return ""
    cmp = report.compare(baseline_id, run_id)
    if cmp is None:
        return (
            '<section class="card"><h2>Baseline vs current</h2>'
            f'<p class="warn">Baseline run not found: <code>{_esc(baseline_id)}</code></p></section>'
        )
    summary = cmp.get("summary") or {}
    rows = cmp.get("cases") or []
    head = (
        "<tr><th>Case</th><th>Type</th><th>Result</th><th>Base F1</th><th>Current F1</th>"
        "<th>Delta</th><th>Halluc</th><th>Recon</th><th>Duration</th><th>AI</th><th>Cost</th></tr>"
    )
    body = []
    for row in rows:
        status = row.get("status") or ""
        status_class = "bad" if status in {"regressed", "missing"} else ("ok" if status == "improved" else "")
        delta_value = row.get("delta_row_f1")
        delta_text = "—" if delta_value is None else f"{delta_value:+.2f}"
        recon_delta = (
            f"{_esc(_bool_symbol(row.get('a_is_reconciled')))} -> "
            f"{_esc(_bool_symbol(row.get('b_is_reconciled')))}"
        )
        body.append(
            "<tr>"
            f"<td>{_esc(row.get('case_id'))}</td>"
            f"<td>{_esc(row.get('call_type'))}</td>"
            f'<td><span class="badge {status_class}">{_esc(status)}</span></td>'
            f"<td>{_esc(row.get('a_row_f1'))}</td>"
            f"<td>{_esc(row.get('b_row_f1'))}</td>"
            f"<td>{_esc(delta_text)}</td>"
            f"<td>{_esc(row.get('a_hallucinations'))} -> {_esc(row.get('b_hallucinations'))}</td>"
            f"<td>{recon_delta}</td>"
            f"<td>{_esc(_format_ms(row.get('a_duration_ms')))} -> {_esc(_format_ms(row.get('b_duration_ms')))}</td>"
            f"<td>{_esc(_format_ms(row.get('a_ai_latency_ms')))} -> {_esc(_format_ms(row.get('b_ai_latency_ms')))}</td>"
            f"<td>${_esc(row.get('a_est_cost_usd'))} -> ${_esc(row.get('b_est_cost_usd'))}</td>"
            "</tr>"
        )
    return (
        '<section class="card"><h2>Baseline vs current</h2>'
        f'<p class="meta">baseline {_esc(baseline_id)} vs current {_esc(run_id)}</p>'
        '<p class="kpis">'
        f'<span>regressed <b>{summary.get("regressed", 0)}</b></span>'
        f'<span>improved <b>{summary.get("improved", 0)}</b></span>'
        f'<span>unchanged <b>{summary.get("unchanged", 0)}</b></span>'
        f'<span>new <b>{summary.get("new", 0)}</b></span>'
        f'<span>missing <b>{summary.get("missing", 0)}</b></span>'
        '</p>'
        f'<div class="scroll"><table><thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div>'
        '</section>'
    )


def render_report_html(run_id: str) -> str | None:
    meta = report.read_run(run_id)
    if meta is None:
        return None

    profile_paths = _profile_paths()
    agg = meta.get("aggregate") or {}

    # group cases by layout (= profile.json), reading each case's full detail.
    groups: dict[str, list[dict[str, Any]]] = {}
    for index in meta.get("cases") or []:
        case = report.read_case(run_id, index["file"])
        if case is None:
            continue
        groups.setdefault(case.get("layout_id") or case["case_id"].split("/")[0], []).append(case)

    kpis = (
        f'<div class="kpis"><span>cases <b>{agg.get("cases", 0)}</b></span>'
        f'<span>mean F1 <b>{agg.get("mean_row_f1")}</b></span>'
        f'<span>hallucinations <b>{agg.get("total_hallucinations", 0)}</b></span>'
        f'<span>reconciled <b>{agg.get("reconciled_pct")}%</b></span>'
        f'<span>cost <b>${agg.get("total_cost_usd", 0)}</b></span></div>'
    )

    body = [
        f"<h1>Eval report — {_esc(meta.get('label') or run_id)}</h1>",
        f'<p class="meta">run {_esc(run_id)} · model {_esc(meta.get("model"))} · '
        f'git {_esc(meta.get("git_sha"))} · {_esc(meta.get("created_at"))} · '
        f'call types: {_esc(", ".join(meta.get("call_types") or []))}</p>',
        kpis,
    ]
    comparison = _comparison_section(meta)
    if comparison:
        body.append(comparison)
    if "extraction" in (meta.get("call_types") or []):
        body.append(_prompt_template_section())

    for layout_id in sorted(groups):
        body.append(f"<h2>{_esc(layout_id)}</h2>")
        path = profile_paths.get(layout_id)
        if path and path.exists():
            try:
                pretty = json.dumps(json.loads(path.read_text(encoding="utf-8")), indent=2)
            except Exception:
                pretty = path.read_text(encoding="utf-8")
            body.append(f"<details><summary>profile.json</summary><pre>{_esc(pretty)}</pre></details>")
        else:
            body.append('<p class="muted">profile.json not found for this layout.</p>')
        body.extend(_case_block(case) for case in groups[layout_id])

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Eval report — {_esc(meta.get('label') or run_id)}</title>"
        f"<style>{_CSS}</style></head><body>{''.join(body)}</body></html>"
    )
