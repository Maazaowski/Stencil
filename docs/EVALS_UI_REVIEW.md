# Evals page — UI/UX review

Interactive walkthrough of `/evals` (frontend `src/app/evals/page.tsx` + case drill-down
`src/app/evals/[runId]/[caseFile]/page.tsx`). Reviewed every control, ran a live 1-case
regression (`att.cloudvoice`, "current" mode) and watched it go queued → running → done,
then inspected the run detail, A→B compare, the case drill-down, and the HTML report.

Ordered by impact. File:line references are to `src/app/evals/page.tsx` unless noted.

---

## Bugs — should fix

### B1. Garbled em-dash (`â€"`) throughout the run detail
`ms()` (`:33`), `delta()` (`:42`), and the regression-compare null cells (`:596, :598, :600,
:602`) return the literal mojibake string `"â€"` instead of `"—"`. Meanwhile the same file
uses a correct `"—"` in `pct()`/`num()` (`:26, :29`), line `:372`, `:393`, `:630`. So on a
real run the regression table's null cells (every "missing" case: duration, AI latency, delta,
recon) render as `â€"`. The standalone HTML report renders `—` correctly, which is the
giveaway. **Fix:** replace the six `"â€"` literals with `"—"`.

### B2. "Compare A→B" table has no horizontal overflow container
The manual A/B compare table (`:351`) is a bare `<table class="w-full">` with **no**
`overflow-auto` wrapper. Long case IDs (e.g. `eunetworks.standard/A837737-IE-SI53564`) push the
table ~118px past its card with `overflow-x: visible`, so the **Status / A / B / Δ columns render
off-screen and are unreachable** (measured: table right 1155px vs card right 1037px vs viewport
1076px). The *regression* compare table (`:563`) does the right thing — wrapped in
`overflow-auto`. **Fix:** wrap the A→B table in a `div.overflow-auto` like the other one.

### B3. Metric labels collide in the regression summary
The summary grid (`:537`, `grid-cols-3 lg:grid-cols-6`) at the detail-column width packs six
~57px-wide columns, so `Hallucinations`, `Reconciled`, `Classification` clip into each other and
read as `HallucinationsReconciledClassificationScored`. Confirmed the three middle labels overflow
their box. **Fix:** let labels wrap, widen columns, or drop to 3 columns on narrow widths.

### B4. Pluralization: "1 cases"
Run-list card (`:312`) and progress line show `1 cases · 4 workers` / `Queued · 1 cases`.
**Fix:** pluralize (`1 case`).

---

## UX / behavior — higher value

### U1. A subset run in "current" mode looks like a catastrophic regression
Running 1 of 38 cases in "current" mode compares that run against the **full** baseline, so the
other 37 cases show as `missing` (all red) and the aggregate deltas read **Row F1 −0.786,
Hallucinations −79, Scored −38, Cost −$11.27** — for what is actually a healthy smoke test. The
verdict logic and colouring make a partial run look like a total collapse.
**Fix:** scope the comparison (deltas + verdict) to the **intersection of cases actually run**;
render "not in this run" distinctly from a genuine `missing`/`regressed`.

### U2. Completed-results / regression card renders while the run is still processing
The regression box (`:503`) appears the moment `run.data.run` exists — i.e. *before any case has
scored* — showing the same alarming all-negative deltas against baseline. A user watching a run
in flight sees "Regressed / −0.786" before a single result is in.
**Fix:** gate the regression box behind run completion (or show a "pending" state until `done`).

### U3. Starting a run while an A/B compare is active hides the new run's progress
Detail precedence (`:340`) is `compare > openRun`. `handleStart` sets `openRun` to the new run,
but if A and B are still set the compare panel keeps the whole detail column, so the new run's
progress panel never shows. After clicking **Run** you get only the tiny list-card.
**Fix:** clear `cmp` (or prioritize an in-flight run) when a run is started.

### U4. Detail column is cramped; big tables force horizontal scroll
The page is a `[360px, 1fr]` two-column grid, so at ~1076px the detail/results get ~340px and the
10-column regression table scrolls horizontally inside it (scrollWidth 833 in a 337px box). Half
the columns are hidden by default. **Fix:** move run detail to a full-width row below the list (or
a dedicated route/modal) so the wide comparison table has room.

### U5. "Baseline" vs "Current" mode is unexplained
The mode toggle has no help text on when to use which; a new operator can't tell that "current"
means "score against the accepted baseline." **Fix:** add a one-line tooltip/hint.

---

## Minor / polish

- **M1.** Git SHA shows `unknown` in Docker (header `· unknown`, `:449/:467`) — hide when unknown.
- **M2.** Case selection is folder-wise only; individual invoices are read-only (`:261`). If a
  layout has many invoices you can't run just one — may be intentional, noting it.
- **M3.** The `current.v15.test` run has been stuck in `Cancelling` with a live progress bar for a
  long time — cancellation may not be terminating (worth confirming the cancel path actually stops
  work; could be backend, but the UI shows an eternal spinner).

## Not UI — flag for separate investigation

- **D1.** The `att.cloudvoice/ATT-6542215115` case scored **F1 0.00** while the drill-down shows
  Actual has 2 of 3 expected rows (missing service ID `54489331`). 2/3 rows should score ~0.67,
  not 0.00 — looks like an eval **row-matching / scoring** issue (or a genuine extraction miss the
  scorer collapses to 0). Worth a look independent of the UI.

---

## What works well (keep)

- **Case drill-down** (`[runId]/[caseFile]`): Expected vs Actual side-by-side + collapsible
  "Prompts sent" (full AI call metadata + system/user prompt) — excellent for debugging.
- **HTML report**: thorough and readable — summary, baseline-vs-current, and the shared prompt
  templates + per-case variable table. More legible than the in-app compare.
- **Live progress**: progress bar + metadata grid (label/mode/work items/model/baseline) while
  running is clear.
- **Selection controls**: Select all / Clear / per-group counts behave correctly; Run disables at
  0 cases.
