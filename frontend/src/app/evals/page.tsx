"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Play, Loader2, FileText, ChevronRight, ChevronDown } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PageHeader } from "@/components/page-header";
import type { EvalCompareRow } from "@/types";
import { cn } from "@/lib/utils";
import { safeFormatDate } from "@/lib/format-date";
import {
  useEvalCases,
  useEvalRuns,
  useEvalRun,
  useStartEvalRun,
  useEvalBaseline,
  useSetEvalBaseline,
  useCompareEvalRuns,
  useCancelEvalRun,
} from "@/hooks/use-evals";

function pct(v: number | null | undefined) {
  return v === null || v === undefined ? "—" : `${v}%`;
}
function num(v: number | null | undefined, digits = 3) {
  return v === null || v === undefined ? "—" : v.toFixed(digits);
}

function ms(v: number | null | undefined) {
  if (v === null || v === undefined) return "—";
  if (v < 1000) return `${v}ms`;
  const seconds = v / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}

function delta(v: number | null | undefined, digits = 3) {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}`;
}

function comparisonVerdict(a?: number | null, b?: number | null) {
  if (a === null || a === undefined || b === null || b === undefined) return "No F1 verdict";
  if (b > a) return "Improved";
  if (b < a) return "Regressed";
  return "No F1 change";
}

function compactList(values: string[] | null | undefined, limit = 4) {
  const items = values ?? [];
  if (items.length === 0) return "All folders";
  if (items.length <= limit) return items.join(", ");
  return `${items.slice(0, limit).join(", ")} +${items.length - limit} more`;
}

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** Small colored chip for a case's verdict vs the baseline. Null when there's no verdict. */
const VERDICT_STYLE: Record<string, string> = {
  improved: "bg-success/12 text-success",
  regressed: "bg-destructive/10 text-destructive",
  missing: "bg-destructive/10 text-destructive",
  new: "bg-primary/10 text-primary",
  unchanged: "bg-muted text-muted-foreground",
};
function VerdictChip({ status }: { status?: string | null }) {
  if (!status || status === "unscored" || !VERDICT_STYLE[status]) return null;
  return (
    <span className={cn("rounded px-1.5 py-0.5 text-[10px] font-medium capitalize", VERDICT_STYLE[status])}>
      {status}
    </span>
  );
}

/** "MMM d" without the git-sha noise when it's unknown. */
function shaSuffix(sha?: string | null) {
  return sha && sha !== "unknown" ? ` · ${sha}` : "";
}

export default function EvalsPage() {
  const cases = useEvalCases();
  const runs = useEvalRuns();
  const baseline = useEvalBaseline();
  const startRun = useStartEvalRun();
  const cancelRun = useCancelEvalRun();
  const setBaseline = useSetEvalBaseline();

  const router = useRouter();
  const [selected, setSelected] = useState<string[]>(["extraction"]);
  const [selectedCaseIds, setSelectedCaseIds] = useState<string[] | null>(null);
  const [runKind, setRunKind] = useState<"baseline" | "current" | null>(null);
  const [label, setLabel] = useState("");
  const [concurrency, setConcurrency] = useState(4);
  const [openRun, setOpenRun] = useState<string | null>(null);
  const [cmp, setCmp] = useState<{ a: string | null; b: string | null }>({ a: null, b: null });
  const [showNotInRun, setShowNotInRun] = useState(false);

  const run = useEvalRun(openRun);
  const manualCompare = useCompareEvalRuns(cmp.a, cmp.b);
  const openedRun = run.data?.run;
  const status = run.data?.status;
  const isRunning = !!status && (status.status === "running" || status.status === "queued" || status.status === "cancelling");
  const isDone = !!openedRun && !isRunning;
  const activeRunKind = runKind ?? (baseline.data?.baseline ? "current" : "baseline");
  // Only compare against the baseline once the run is DONE: mid-run the current run has no
  // authoritative run.json, so the compare would read it as empty (every case "missing") and
  // React Query would cache that stale result. Gating on isDone keeps verdicts correct.
  const regressionBaselineId = isDone && openedRun?.run_kind === "current" ? openedRun.baseline_run_id ?? null : null;
  const regressionCompare = useCompareEvalRuns(regressionBaselineId, openedRun?.run_id ?? null);

  const callTypes = cases.data?.call_types ?? ["classification", "extraction", "model_authoring", "profile_authoring"];
  const allCases = useMemo(() => cases.data?.cases ?? [], [cases.data?.cases]);
  const allCaseIds = useMemo(() => allCases.map((c) => c.case_id), [allCases]);
  const activeCaseIds = selectedCaseIds ?? allCaseIds;
  const caseGroups = useMemo(() => {
    const groups = new Map<string, typeof allCases>();
    for (const c of allCases) {
      groups.set(c.layout_id, [...(groups.get(c.layout_id) ?? []), c]);
    }
    return Array.from(groups.entries()).map(([layoutId, groupCases]) => ({
      layoutId,
      cases: groupCases,
    }));
  }, [allCases]);

  async function handleStart() {
    if (selected.length === 0) return toast.error("Pick at least one call type.");
    if (activeCaseIds.length === 0) return toast.error("Pick at least one test case.");
    try {
      const res = await startRun.mutateAsync({
        call_types: selected,
        label,
        concurrency,
        case_ids: activeCaseIds,
        run_kind: activeRunKind,
      });
      setOpenRun(res.run_id);
      setCmp({ a: null, b: null });   // show the new run's live progress, not a stale compare
      setLabel("");
      toast.success("Eval run started.");
    } catch {
      toast.error("Could not start the run (is ST_DEBUG on?).");
    }
  }

  function toggle(ct: string) {
    setSelected((prev) => (prev.includes(ct) ? prev.filter((c) => c !== ct) : [...prev, ct]));
  }

  function toggleGroup(layoutId: string) {
    const groupIds = allCases.filter((c) => c.layout_id === layoutId).map((c) => c.case_id);
    const selectedSet = new Set(activeCaseIds);
    const allSelected = groupIds.every((id) => selectedSet.has(id));
    for (const id of groupIds) {
      if (allSelected) selectedSet.delete(id);
      else selectedSet.add(id);
    }
    setSelectedCaseIds(Array.from(selectedSet));
  }

  async function acceptBaseline(runId: string) {
    try {
      await setBaseline.mutateAsync(runId);
      toast.success("Baseline updated.");
    } catch {
      toast.error("Could not update baseline.");
    }
  }

  const runList = runs.data?.runs ?? [];
  const progressPct = status?.total ? Math.round(((status.done ?? 0) / status.total) * 100) : 0;
  const runMeta = openedRun ?? status ?? null;
  const runLabel = runMeta?.label || openRun || "";
  const runLayouts = runMeta?.selected_layouts ?? [];
  const runCallTypes = runMeta?.call_types ?? [];

  // Join the run's own cases with the baseline comparison so one table shows both the
  // current score and the delta/verdict. The compare row lacks `file`; the run case has it.
  const cmpByKey = useMemo(() => {
    const m = new Map<string, EvalCompareRow>();
    for (const r of regressionCompare.data?.cases ?? []) m.set(`${r.case_id}:${r.call_type}`, r);
    return m;
  }, [regressionCompare.data]);
  const hasBaselineCmp = !!regressionBaselineId && !!regressionCompare.data;
  const runCases = useMemo(() => openedRun?.cases ?? [], [openedRun]);
  // Baseline cases with no result in THIS run — shown as a collapsed strip, not red rows.
  const notInRun = useMemo(() => {
    if (!hasBaselineCmp) return [] as EvalCompareRow[];
    const ran = new Set(runCases.map((c) => `${c.case_id}:${c.call_type}`));
    return (regressionCompare.data?.cases ?? []).filter((r) => !ran.has(`${r.case_id}:${r.call_type}`));
  }, [hasBaselineCmp, regressionCompare.data, runCases]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Evals"
        description="Run each prompt against labeled cases and score the delivered Excel — row accuracy, hallucinations, reconciliation, cost. Compare two runs to prove a prompt change helped."
      />

      {/* New run */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">New run</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <div className="inline-flex rounded-md border p-0.5">
              {(["baseline", "current"] as const).map((kind) => (
                <button
                  key={kind}
                  onClick={() => setRunKind(kind)}
                  className={cn(
                    "rounded px-3 py-1 text-xs capitalize",
                    activeRunKind === kind ? "bg-primary text-primary-foreground" : "text-muted-foreground",
                  )}
                >
                  {kind}
                </button>
              ))}
            </div>
            {baseline.data?.baseline ? (
              <p className="text-xs text-muted-foreground">
                Active baseline: <span className="font-mono">{baseline.data.baseline.run_id}</span>
              </p>
            ) : (
              <p className="text-xs text-muted-foreground">No accepted baseline yet.</p>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            {activeRunKind === "current"
              ? "Current scores this run against the accepted baseline to catch regressions."
              : "Baseline captures a fresh reference you can promote for future runs to compare against."}
          </p>
          <div className="flex flex-wrap items-end gap-4">
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">Call types</p>
            <div className="flex flex-wrap gap-2">
              {callTypes.map((ct) => (
                <button
                  key={ct}
                  onClick={() => toggle(ct)}
                  className={cn(
                    "rounded-md border px-2 py-1 text-xs",
                    selected.includes(ct) ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground",
                  )}
                >
                  {ct}
                </button>
              ))}
            </div>
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">Label (e.g. prompt version)</p>
            <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="baseline" className="h-8 w-48" />
          </div>
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">Concurrency</p>
            <Input
              type="number"
              min={1}
              max={16}
              value={concurrency}
              onChange={(e) => setConcurrency(Math.max(1, Math.min(16, Number(e.target.value) || 1)))}
              className="h-8 w-20"
            />
          </div>
          <Button size="sm" onClick={handleStart} disabled={startRun.isPending || allCases.length === 0 || activeCaseIds.length === 0}>
            {startRun.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            Run
          </Button>
          </div>
          {allCases.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">
              No labeled cases. Add one under <span className="font-mono">eval_cases/&lt;layout&gt;/</span> —
              {" "}<span className="font-mono">profile.json</span>, <span className="font-mono">invoices/&lt;name&gt;.pdf</span>,
              {" "}<span className="font-mono">expected/&lt;name&gt;.xlsx</span> (see eval_cases/README.md).
            </p>
          ) : (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-xs text-muted-foreground">
                  Test cases: {activeCaseIds.length}/{allCases.length} selected
                </p>
                <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => setSelectedCaseIds(null)}>
                  Select all
                </Button>
                <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => setSelectedCaseIds([])}>
                  Clear
                </Button>
              </div>
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {caseGroups.map((group) => {
                  const ids = group.cases.map((c) => c.case_id);
                  const selectedInGroup = ids.filter((id) => activeCaseIds.includes(id)).length;
                  const all = selectedInGroup === ids.length;
                  const some = selectedInGroup > 0 && !all;
                  return (
                    // Selection is folder-wise; clicking anywhere on the card toggles the group.
                    <label
                      key={group.layoutId}
                      className={cn(
                        "flex cursor-pointer flex-col rounded-lg border p-3 transition-colors",
                        all
                          ? "border-primary/40 bg-primary/5"
                          : some
                            ? "border-primary/25"
                            : "border-border hover:bg-muted/50",
                      )}
                    >
                      <div className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={all}
                          ref={(el) => { if (el) el.indeterminate = some; }}
                          onChange={() => toggleGroup(group.layoutId)}
                          className="size-4 accent-primary"
                        />
                        <span className="truncate text-sm font-medium" title={group.layoutId}>{group.layoutId}</span>
                        <Badge variant={all ? "default" : "secondary"} className="ml-auto text-[10px] tabular-nums">
                          {selectedInGroup}/{ids.length}
                        </Badge>
                      </div>
                      <div className="mt-2 max-h-24 space-y-0.5 overflow-auto pl-6">
                        {group.cases.map((c) => (
                          <p key={c.case_id} className="truncate text-[11px] text-muted-foreground" title={c.case_id}>
                            {c.case_id.split("/").slice(1).join("/") || c.case_id}
                          </p>
                        ))}
                      </div>
                    </label>
                  );
                })}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,300px)_minmax(0,1fr)]">
        {/* Runs list */}
        <div className="space-y-2">
          {runList.length === 0 ? (
            <Card><CardContent className="py-8 text-center text-sm text-muted-foreground">No runs yet.</CardContent></Card>
          ) : (
            runList.map((r) => (
              <button
                key={r.run_id}
                onClick={() => setOpenRun(r.run_id)}
                className={cn(
                  "w-full rounded-md border px-3 py-2 text-left text-xs",
                  openRun === r.run_id ? "border-primary bg-primary/5" : "border-border hover:bg-muted",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{r.label || r.run_id}</span>
                  <span className="text-muted-foreground">{safeFormatDate(r.created_at, "MMM d HH:mm")}</span>
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {r.status && r.status !== "done" && (
                    <Badge variant="outline" className="text-[10px] capitalize">{r.status}</Badge>
                  )}
                  {r.run_kind && <Badge variant="outline" className="text-[10px]">{r.run_kind}</Badge>}
                  {baseline.data?.baseline?.run_id === r.run_id && <Badge className="text-[10px]">baseline</Badge>}
                  {r.call_types.map((c) => <Badge key={c} variant="secondary" className="text-[10px]">{c}</Badge>)}
                </div>
                <div className="mt-1 text-muted-foreground">
                  F1 {num(r.aggregate?.mean_row_f1, 2)} · halluc {r.aggregate?.total_hallucinations ?? 0} · recon {pct(r.aggregate?.reconciled_pct)} · ${num(r.aggregate?.total_cost_usd, 3)}
                </div>
                {r.status && r.status !== "done" && (
                  <div className="mt-1 space-y-1 text-muted-foreground">
                    <div>
                      {r.status === "queued" ? "Queued" : `Processing ${r.done ?? 0}/${r.total ?? r.total_work_items ?? 0}`}
                      {r.selected_case_count ? ` · ${plural(r.selected_case_count, "case")}` : ""}
                      {r.concurrency ? ` · ${plural(r.concurrency, "worker")}` : ""}
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-sm bg-muted">
                      <div
                        className="h-full rounded-sm bg-primary"
                        style={{ width: `${r.total ? Math.round(((r.done ?? 0) / r.total) * 100) : 0}%` }}
                      />
                    </div>
                  </div>
                )}
                <div className="mt-1 flex gap-2 text-[10px]">
                  <button
                    onClick={(e) => { e.stopPropagation(); setCmp((c) => ({ ...c, a: r.run_id })); }}
                    className={cn("rounded border px-1", cmp.a === r.run_id ? "border-primary text-primary" : "border-border text-muted-foreground")}
                  >A</button>
                  <button
                    onClick={(e) => { e.stopPropagation(); setCmp((c) => ({ ...c, b: r.run_id })); }}
                    className={cn("rounded border px-1", cmp.b === r.run_id ? "border-primary text-primary" : "border-border text-muted-foreground")}
                  >B</button>
                </div>
              </button>
            ))
          )}
        </div>

        {/* Detail */}
        <div className="space-y-4">
          {cmp.a && cmp.b ? (
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">Compare A→B (row F1 delta)</CardTitle></CardHeader>
              <CardContent>
                {manualCompare.data?.summary && (
                  <p className="mb-2 text-xs text-muted-foreground">
                    {manualCompare.data.summary.regressed ?? 0} regressed · {manualCompare.data.summary.improved ?? 0} improved · {manualCompare.data.summary.unchanged ?? 0} unchanged
                    {(manualCompare.data.summary.new ?? 0) > 0 && <> · {manualCompare.data.summary.new} new</>}
                    {(manualCompare.data.summary.missing ?? 0) > 0 && <> · {manualCompare.data.summary.missing} missing</>}
                  </p>
                )}
                <Table className="text-xs">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="h-8">Case</TableHead>
                      <TableHead className="h-8 text-center">Status</TableHead>
                      <TableHead className="h-8 text-center">A</TableHead>
                      <TableHead className="h-8 text-center">B</TableHead>
                      <TableHead className="h-8 text-center">Δ</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(manualCompare.data?.cases ?? []).map((row, i) => (
                      <TableRow key={i}>
                        <TableCell className="py-1.5 font-mono text-[11px]">{row.case_id}</TableCell>
                        <TableCell className="py-1.5 text-center"><VerdictChip status={row.status} /></TableCell>
                        <TableCell className="py-1.5 text-center tabular-nums">{num(row.a_row_f1, 2)}</TableCell>
                        <TableCell className="py-1.5 text-center tabular-nums">{num(row.b_row_f1, 2)}</TableCell>
                        <TableCell className={cn("py-1.5 text-center font-medium tabular-nums", (row.delta_row_f1 ?? 0) > 0 ? "text-success" : (row.delta_row_f1 ?? 0) < 0 ? "text-destructive" : "")}>
                          {row.delta_row_f1 === null ? "—" : delta(row.delta_row_f1, 2)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
                <Button size="sm" variant="ghost" className="mt-2 text-xs" onClick={() => setCmp({ a: null, b: null })}>Clear compare</Button>
              </CardContent>
            </Card>
          ) : !openRun ? (
            <Card><CardContent className="py-10 text-center text-sm text-muted-foreground">Select a run, or start one above.</CardContent></Card>
          ) : (
            <>
              {status && status.status !== "done" && (
                <Card><CardContent className="space-y-2 py-3 text-sm text-muted-foreground">
                  <div className="flex items-center gap-2">
                    {(status.status === "running" || status.status === "cancelling") && <Loader2 className="h-4 w-4 animate-spin" />}
                    <span className="flex-1">
                      {status.status === "error"
                        ? `Failed: ${status.detail}`
                        : status.status === "cancelled"
                          ? `Cancelled — ${status.done ?? 0}/${status.total ?? 0} processed (partial results kept)`
                          : status.status === "cancelling"
                            ? `Cancelling after in-flight work finishes - ${status.done ?? 0}/${status.total ?? 0} processed`
                            : `Processing ${status.done ?? 0} / ${status.total ?? 0} invoices`}
                    </span>
                    {(status.status === "running" || status.status === "queued") && openRun && (
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 text-xs"
                        disabled={cancelRun.isPending}
                        onClick={() => cancelRun.mutate(openRun)}
                      >
                        {cancelRun.isPending ? "Cancelling…" : "Cancel"}
                      </Button>
                    )}
                  </div>
                  {(status.status === "running" || status.status === "queued" || status.status === "cancelling") && (
                    <div className="h-2 w-full overflow-hidden rounded-sm bg-muted">
                      <div
                        className="h-full rounded-sm bg-primary transition-all duration-500"
                        style={{ width: `${progressPct}%` }}
                      />
                    </div>
                  )}
                  <div className="grid gap-3 pt-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
                    <div>
                      <p className="text-muted-foreground">Label</p>
                      <p className="truncate font-medium text-foreground" title={runLabel}>{runLabel || "Unlabeled run"}</p>
                    </div>
                    <div>
                      <p className="text-muted-foreground">Mode</p>
                      <p className="font-medium capitalize text-foreground">{runMeta?.run_kind || "unknown"}</p>
                    </div>
                    <div>
                      <p className="text-muted-foreground">Call Types</p>
                      <p className="truncate font-medium text-foreground" title={runCallTypes.join(", ")}>{runCallTypes.join(", ") || "unknown"}</p>
                    </div>
                    <div>
                      <p className="text-muted-foreground">Selected Cases</p>
                      <p className="font-medium text-foreground">{runMeta?.selected_case_count ?? "unknown"}</p>
                    </div>
                    <div>
                      <p className="text-muted-foreground">Concurrency</p>
                      <p className="font-medium text-foreground">{runMeta?.concurrency ?? "unknown"}</p>
                    </div>
                    <div>
                      <p className="text-muted-foreground">Work Items</p>
                      <p className="font-medium text-foreground">{status.done ?? 0}/{status.total ?? runMeta?.total_work_items ?? "unknown"}</p>
                    </div>
                    <div>
                      <p className="text-muted-foreground">Model</p>
                      <p className="truncate font-medium text-foreground" title={runMeta?.model}>{runMeta?.model || "unknown"}</p>
                    </div>
                    <div>
                      <p className="text-muted-foreground">Git SHA</p>
                      <p className="font-mono text-foreground">{runMeta?.git_sha || "unknown"}</p>
                    </div>
                    <div className="sm:col-span-2 xl:col-span-4">
                      <p className="text-muted-foreground">Folders</p>
                      <p className="truncate font-medium text-foreground" title={runLayouts.join(", ")}>{compactList(runLayouts)}</p>
                    </div>
                    {runMeta?.baseline_run_id && (
                      <div className="sm:col-span-2 xl:col-span-4">
                        <p className="text-muted-foreground">Captured Baseline</p>
                        <p className="truncate font-mono text-foreground" title={runMeta.baseline_run_id}>{runMeta.baseline_run_id}</p>
                      </div>
                    )}
                  </div>
                </CardContent></Card>
              )}
              {run.data?.run && (
                <Card>
                  <CardHeader className="flex flex-row items-center justify-between gap-3 pb-2">
                    <CardTitle className="text-sm">{run.data.run.label || run.data.run.run_id} · {run.data.run.model}{shaSuffix(run.data.run.git_sha)}</CardTitle>
                    <div className="flex flex-wrap items-center justify-end gap-2">
                      {baseline.data?.baseline?.run_id === run.data.run.run_id ? (
                        <Badge className="text-xs">Active baseline</Badge>
                      ) : isDone ? (
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 text-xs"
                          disabled={setBaseline.isPending}
                          onClick={() => acceptBaseline(run.data!.run!.run_id)}
                        >
                          {run.data.run.run_kind === "baseline" ? "Accept as baseline" : "Promote to baseline"}
                        </Button>
                      ) : null}
                      <a
                        href={`/api/v1/debug/evals/runs/${openRun}/report.html`}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                      >
                        <FileText className="h-3.5 w-3.5" /> Report
                      </a>
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    {/* A subset run can't yield a meaningful aggregate verdict (its mean is
                        compared against the FULL baseline), so only show it for a full run. */}
                    {isDone && hasBaselineCmp && notInRun.length > 0 && (
                      <p className="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground">
                        Partial run — {plural(runCases.length, "case")} of {runCases.length + notInRun.length}. Per-case deltas below compare to the baseline; run all cases for an aggregate regression verdict.
                      </p>
                    )}
                    {/* Baseline verdict + aggregate deltas — only once the run is complete AND every
                        baseline case ran, so a partial run never shows an alarming "Regressed". */}
                    {isDone && hasBaselineCmp && notInRun.length === 0 && regressionCompare.data && (
                      <div className="rounded-md border p-3 text-xs">
                        <p className={cn(
                          "text-sm font-medium",
                          comparisonVerdict(regressionCompare.data.a?.mean_row_f1, regressionCompare.data.b?.mean_row_f1) === "Improved" && "text-success",
                          comparisonVerdict(regressionCompare.data.a?.mean_row_f1, regressionCompare.data.b?.mean_row_f1) === "Regressed" && "text-destructive",
                        )}>
                          {comparisonVerdict(regressionCompare.data.a?.mean_row_f1, regressionCompare.data.b?.mean_row_f1)}
                        </p>
                        <p className="text-muted-foreground">
                          Baseline {regressionCompare.data.a_id} vs current {regressionCompare.data.b_id}
                        </p>
                        <div className="mt-3 grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(96px,1fr))]">
                          <div>
                            <p className="text-muted-foreground">Row F1</p>
                            <p className="tabular-nums">{num(regressionCompare.data.b?.mean_row_f1, 3)} <span className="text-muted-foreground">({delta((regressionCompare.data.b?.mean_row_f1 ?? 0) - (regressionCompare.data.a?.mean_row_f1 ?? 0), 3)})</span></p>
                          </div>
                          <div>
                            <p className="text-muted-foreground">Hallucinations</p>
                            <p className="tabular-nums">{regressionCompare.data.b?.total_hallucinations ?? 0} <span className="text-muted-foreground">({delta((regressionCompare.data.b?.total_hallucinations ?? 0) - (regressionCompare.data.a?.total_hallucinations ?? 0), 0)})</span></p>
                          </div>
                          <div>
                            <p className="text-muted-foreground">Reconciled</p>
                            <p className="tabular-nums">{pct(regressionCompare.data.b?.reconciled_pct)} <span className="text-muted-foreground">({delta((regressionCompare.data.b?.reconciled_pct ?? 0) - (regressionCompare.data.a?.reconciled_pct ?? 0), 1)})</span></p>
                          </div>
                          <div>
                            <p className="text-muted-foreground">Classification</p>
                            <p className="tabular-nums">{pct(regressionCompare.data.b?.classification_pct)} <span className="text-muted-foreground">({delta((regressionCompare.data.b?.classification_pct ?? 0) - (regressionCompare.data.a?.classification_pct ?? 0), 1)})</span></p>
                          </div>
                          <div>
                            <p className="text-muted-foreground">Scored</p>
                            <p className="tabular-nums">{regressionCompare.data.b?.scored_cases ?? 0} <span className="text-muted-foreground">({delta((regressionCompare.data.b?.scored_cases ?? 0) - (regressionCompare.data.a?.scored_cases ?? 0), 0)})</span></p>
                          </div>
                          <div>
                            <p className="text-muted-foreground">Cost</p>
                            <p className="tabular-nums">${num(regressionCompare.data.b?.total_cost_usd, 3)} <span className="text-muted-foreground">({delta((regressionCompare.data.b?.total_cost_usd ?? 0) - (regressionCompare.data.a?.total_cost_usd ?? 0), 3)})</span></p>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Live count while streaming. */}
                    {isRunning && (
                      <p className="text-xs text-muted-foreground">
                        Streaming results — {runCases.length}/{status?.total ?? "?"} cases done
                      </p>
                    )}

                    {/* Baseline cases that didn't run here — collapsed, not shown as red "missing" rows. */}
                    {isDone && notInRun.length > 0 && (
                      <div className="rounded-md border text-xs">
                        <button
                          onClick={() => setShowNotInRun((v) => !v)}
                          className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-muted-foreground hover:bg-muted/50"
                        >
                          {showNotInRun ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                          {plural(notInRun.length, "baseline case")} not in this run
                        </button>
                        {showNotInRun && (
                          <div className="max-h-40 space-y-0.5 overflow-auto border-t px-3 py-2">
                            {notInRun.map((r) => (
                              <p key={`${r.case_id}:${r.call_type}`} className="truncate font-mono text-[11px] text-muted-foreground" title={r.case_id}>{r.case_id}</p>
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    {/* One clickable results table: current score + inline baseline delta/verdict. */}
                    <Table className="text-xs">
                      <TableHeader>
                        <TableRow>
                          <TableHead className="h-8">Case</TableHead>
                          <TableHead className="h-8">Type</TableHead>
                          {hasBaselineCmp && <TableHead className="h-8 text-center">Verdict</TableHead>}
                          <TableHead className="h-8 text-center">Row F1</TableHead>
                          <TableHead className="h-8 text-center">Halluc</TableHead>
                          <TableHead className="h-8 text-center">Recon</TableHead>
                          <TableHead className="h-8 text-center">Duration</TableHead>
                          <TableHead className="h-8 text-center">$</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {runCases.map((c) => {
                          const cmpRow = cmpByKey.get(`${c.case_id}:${c.call_type}`);
                          const d = cmpRow?.delta_row_f1;
                          return (
                            <TableRow
                              key={c.file}
                              onClick={() => router.push(`/evals/${openRun}/${encodeURIComponent(c.file)}`)}
                              className="cursor-pointer"
                            >
                              <TableCell className="py-1.5 font-mono text-[11px]">{c.case_id}</TableCell>
                              <TableCell className="py-1.5 text-muted-foreground">{c.call_type}</TableCell>
                              {hasBaselineCmp && (
                                <TableCell className="py-1.5 text-center"><VerdictChip status={cmpRow?.status} /></TableCell>
                              )}
                              <TableCell className="py-1.5 text-center tabular-nums">
                                {c.status === "success" || c.row_f1 != null ? num(c.row_f1, 2) : (c.status ?? "—")}
                                {hasBaselineCmp && d != null && Math.abs(d) >= 0.005 && (
                                  <span className={cn("ml-1.5 text-[10px]", d > 0 ? "text-success" : "text-destructive")}>
                                    {delta(d, 2)}
                                  </span>
                                )}
                              </TableCell>
                              <TableCell className={cn("py-1.5 text-center tabular-nums", (c.hallucinations ?? 0) > 0 && "text-warning")}>{c.hallucinations ?? 0}</TableCell>
                              <TableCell className="py-1.5 text-center">{c.is_reconciled === null || c.is_reconciled === undefined ? "—" : c.is_reconciled ? "✓" : "✗"}</TableCell>
                              <TableCell className="py-1.5 text-center tabular-nums">{ms(c.duration_ms)}</TableCell>
                              <TableCell className="py-1.5 text-center tabular-nums">{num(c.est_cost_usd, 3)}</TableCell>
                            </TableRow>
                          );
                        })}
                        {isRunning && runCases.length === 0 && (
                          <TableRow>
                            <TableCell colSpan={hasBaselineCmp ? 8 : 7} className="py-6 text-center text-muted-foreground">
                              Waiting for the first case to finish…
                            </TableCell>
                          </TableRow>
                        )}
                      </TableBody>
                    </Table>
                  </CardContent>
                </Card>
              )}

            </>
          )}
        </div>
      </div>
    </div>
  );
}
