"use client";

import { useEffect, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { parseApiDate } from "@/lib/format-date";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  ScanLine,
  Sparkles,
  ShieldCheck,
  CheckCircle2,
  XCircle,
  Loader2,
  Circle,
  Coins,
  Clock,
  RefreshCw,
} from "lucide-react";
import type { ModelTrainingRun, TrainingSetInvoice } from "@/hooks/use-models";

const PHASES = [
  { key: "extracting", label: "Extract ground truth", icon: ScanLine },
  { key: "authoring", label: "Author rules", icon: Sparkles },
  { key: "validating", label: "Validate", icon: ShieldCheck },
  { key: "done", label: "Done", icon: CheckCircle2 },
] as const;

// Fine-grained backend steps map onto the 4 headline phases. testing / holdout /
// saving are sub-steps of "Validate".
const STEP_TO_PHASE: Record<string, number> = {
  extracting: 0,
  authoring: 1,
  validating: 2,
  testing: 2,
  holdout: 2,
  saving: 2,
  done: 3,
};

function phaseIndex(step: string | null | undefined): number {
  return STEP_TO_PHASE[step ?? ""] ?? 0;
}

function fmtElapsed(ms: number): string {
  if (ms < 0) ms = 0;
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return m > 0 ? `${m}m ${rem.toString().padStart(2, "0")}s` : `${rem}s`;
}

interface PerInvoiceDetail {
  ok?: boolean;
  reason?: string;
  expected_lines?: number | null;
  actual_lines?: number | null;
}

type InvoicePhase =
  | "queued"
  | "extracting"
  | "extracted"
  | "testing"
  | "validating"
  | "passed"
  | "failed";

interface InvoiceState {
  filename?: string | null;
  role?: "train" | "holdout";
  state?: InvoicePhase;
  reason?: string | null;
}

export function TrainingProgress({
  run,
  running,
  armed,
  invoices,
}: {
  run: ModelTrainingRun | null;
  running: boolean;
  armed: boolean;
  invoices: TrainingSetInvoice[];
}) {
  // Live-ticking clock so the elapsed timer moves between polls.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running]);

  // Just-armed, before the worker has created the run row.
  if (armed && !run) {
    return (
      <div className="rounded-xl border border-primary/30 bg-primary/5 p-5">
        <div className="flex items-center gap-3">
          <Loader2 className="h-5 w-5 animate-spin text-primary" />
          <div>
            <p className="text-sm font-semibold">Starting training run…</p>
            <p className="text-xs text-muted-foreground">
              Spinning up the worker and queuing your invoices.
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (!run) return null;

  const failed = run.state === "failed";
  const success = run.state === "success";
  const active = phaseIndex(run.step);
  const pct =
    run.total_steps > 0
      ? Math.round((run.completed_steps / run.total_steps) * 100)
      : null;
  const startedMs = parseApiDate(run.started_at)?.getTime() ?? null;
  const finishedMs = parseApiDate(run.finished_at)?.getTime() ?? null;
  const endRef = running ? now : finishedMs ?? now;
  const elapsed = startedMs != null ? endRef - startedMs : null;

  const detail = (run.detail ?? {}) as {
    per_invoice?: Record<string, PerInvoiceDetail | boolean>;
    invoices?: Record<string, InvoiceState>;
    attempts?: number | null;
  };
  const perInvoice = detail.per_invoice ?? {};
  const invoiceStates = detail.invoices ?? {};
  const attempts = detail.attempts ?? null;
  const cost = Number(run.estimated_cost_usd ?? 0);

  // Authoritative per-invoice rows from the run's state map (train + holdout),
  // falling back to the passed-in training set for older runs.
  const stateRows = Object.entries(invoiceStates).map(([intake_id, s]) => ({
    intake_id,
    filename: s.filename ?? intake_id,
    role: s.role ?? "train",
    state: s.state ?? "queued",
    reason: s.reason ?? null,
  }));
  const rows = stateRows.length
    ? stateRows
    : invoices.map((inv) => ({
        intake_id: inv.intake_id,
        filename: inv.filename ?? inv.intake_id,
        role: "train" as const,
        state: "queued" as const,
        reason: null as string | null,
      }));

  // Sub-steps of the "Validate" phase, with live counts derived from the rows.
  const step = run.step ?? "";
  const reachedValidate = phaseIndex(step) >= 2;
  const trainRows = rows.filter((r) => r.role !== "holdout");
  const holdoutRows = rows.filter((r) => r.role === "holdout");
  const isResolved = (r: { intake_id: string; state: string }) =>
    perInvoice[r.intake_id] != null || r.state === "passed" || r.state === "failed";
  const testedCount = trainRows.filter(isResolved).length;
  const holdoutDoneCount = holdoutRows.filter(isResolved).length;
  const subStepState = (active: boolean, done: boolean) =>
    done ? "done" : active ? "active" : "pending";
  const subSteps = [
    {
      key: "testing",
      label: "Test on training",
      count: `${testedCount}/${trainRows.length}`,
      state: subStepState(step === "testing", success || ["holdout", "saving", "done"].includes(step)),
    },
    ...(holdoutRows.length
      ? [{
          key: "holdout",
          label: "Validate on holdout",
          count: `${holdoutDoneCount}/${holdoutRows.length}`,
          state: subStepState(step === "holdout", success || ["saving", "done"].includes(step)),
        }]
      : []),
    {
      key: "saving",
      label: "Save model",
      count: "",
      state: subStepState(step === "saving", success),
    },
  ];

  const accent = failed
    ? "border-destructive/30 bg-destructive/12"
    : success
      ? "border-success/30 bg-success/12"
      : "border-primary/30 bg-gradient-to-br from-primary/5 to-transparent";

  return (
    <div className={cn("rounded-xl border p-5 space-y-5", accent)}>
      {/* Title row */}
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <span
            className={cn(
              "flex h-9 w-9 items-center justify-center rounded-sm",
              failed
                ? "bg-destructive/12 text-destructive"
                : success
                  ? "bg-success/12 text-success"
                  : "bg-primary/15 text-primary",
            )}
          >
            {failed ? (
              <XCircle className="h-5 w-5" />
            ) : success ? (
              <CheckCircle2 className="h-5 w-5" />
            ) : (
              <Loader2 className="h-5 w-5 animate-spin" />
            )}
          </span>
          <div>
            <p className="text-sm font-semibold leading-tight">
              {failed
                ? "Training failed"
                : success
                  ? "Training complete"
                  : "Training in progress"}
            </p>
            <p className="text-xs text-muted-foreground">
              {run.message ?? "Working…"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3 text-xs">
          {elapsed != null && (
            <span className="inline-flex items-center gap-1 text-muted-foreground">
              <Clock className="h-3.5 w-3.5" />
              {fmtElapsed(elapsed)}
            </span>
          )}
          {attempts != null && attempts > 0 && (
            <Badge variant="secondary" className="gap-1">
              <RefreshCw className="h-3 w-3" />
              attempt {attempts}
            </Badge>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <div className="space-y-1.5">
        <Progress
          value={pct ?? 0}
          indeterminate={running && pct == null}
          indicatorClassName={
            failed ? "bg-destructive/12" : success ? "bg-success/12" : "bg-primary"
          }
        />
        {pct != null && (
          <div className="flex justify-between text-[11px] text-muted-foreground">
            <span>
              {run.completed_steps} / {run.total_steps} steps
            </span>
            <span>{pct}%</span>
          </div>
        )}
      </div>

      {/* Phase timeline */}
      <div className="grid grid-cols-4 gap-2">
        {PHASES.map((phase, i) => {
          const Icon = phase.icon;
          const done = success ? true : i < active;
          const current = !success && !failed && i === active;
          const errored = failed && i === active;
          return (
            <div
              key={phase.key}
              className={cn(
                "flex flex-col items-center gap-1.5 rounded-lg border px-2 py-2.5 text-center transition-colors",
                current && "border-primary/50 bg-primary/10",
                done && "border-success/30 bg-success/12",
                errored && "border-destructive/30 bg-destructive/12",
                !current && !done && !errored && "border-muted bg-muted/20",
              )}
            >
              <span
                className={cn(
                  "flex h-7 w-7 items-center justify-center rounded-sm",
                  current && "bg-primary/20 text-primary",
                  done && "bg-success/12 text-success",
                  errored && "bg-destructive/12 text-destructive",
                  !current && !done && !errored && "bg-muted text-muted-foreground",
                )}
              >
                {done ? (
                  <CheckCircle2 className="h-4 w-4" />
                ) : errored ? (
                  <XCircle className="h-4 w-4" />
                ) : current ? (
                  <Icon className="h-4 w-4 animate-pulse" />
                ) : (
                  <Icon className="h-4 w-4" />
                )}
              </span>
              <span className="text-[11px] font-medium leading-tight">
                {phase.label}
              </span>
            </div>
          );
        })}
      </div>

      {/* Validate sub-steps — children of the "Validate" phase */}
      {reachedValidate && (
        <div className="flex flex-wrap items-center gap-2">
          {subSteps.map((s) => {
            const errored = failed && run.step === s.key;
            return (
              <div
                key={s.key}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px]",
                  errored && "border-destructive/30 bg-destructive/12 text-destructive",
                  !errored && s.state === "active" && "border-primary/50 bg-primary/10 text-primary",
                  !errored && s.state === "done" &&
                    "border-success/30 bg-success/12 text-success",
                  !errored && s.state === "pending" && "border-muted bg-muted/20 text-muted-foreground",
                )}
              >
                {errored ? (
                  <XCircle className="h-3.5 w-3.5 shrink-0" />
                ) : s.state === "done" ? (
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                ) : s.state === "active" ? (
                  <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
                ) : (
                  <Circle className="h-3.5 w-3.5 shrink-0" />
                )}
                <span className="font-medium">{s.label}</span>
                {s.count && <span className="opacity-70">{s.count}</span>}
              </div>
            );
          })}
        </div>
      )}

      {/* Per-invoice checklist — authoritative state from the run (train + holdout) */}
      {rows.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Invoices
          </p>
          <div className="space-y-1">
            {rows.map((row) => {
              const raw = perInvoice[row.intake_id];
              const repro: PerInvoiceDetail | null =
                typeof raw === "boolean" ? { ok: raw } : (raw ?? null);
              // Once a result lands (validation done), show reproduced/mismatch;
              // otherwise show the extraction state from the per-invoice map.
              let icon: ReactNode;
              let label: string;
              if (repro != null) {
                icon = repro.ok ? (
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success" />
                ) : (
                  <XCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />
                );
                label = repro.ok ? "reproduced" : (repro.reason ?? "mismatch");
              } else if (row.state === "passed") {
                icon = <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success" />;
                label = "reproduced";
              } else if (row.state === "testing" || row.state === "validating") {
                icon = <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />;
                label = row.state === "validating" ? "validating…" : "testing…";
              } else if (row.state === "extracting") {
                icon = <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />;
                label = "extracting…";
              } else if (row.state === "extracted") {
                icon = <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />;
                label = "extracted";
              } else if (row.state === "failed") {
                icon = <XCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />;
                label = row.reason ?? "failed";
              } else {
                icon = <Circle className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />;
                label = "queued";
              }
              return (
                <div
                  key={row.intake_id}
                  className="flex items-center gap-2 rounded-md bg-background/60 px-2.5 py-1.5 text-xs"
                >
                  {icon}
                  <Badge
                    variant={row.role === "holdout" ? "outline" : "secondary"}
                    className="shrink-0 px-1.5 py-0 text-[10px]"
                  >
                    {row.role === "holdout" ? "Holdout" : "Train"}
                  </Badge>
                  <span className="truncate font-mono">{row.filename}</span>
                  <span className="ml-auto shrink-0 text-muted-foreground">{label}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Footer stats */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t pt-3 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center gap-1">
          <Coins className="h-3.5 w-3.5" />
          ${cost.toFixed(4)}
        </span>
        <span>
          {run.tokens_input.toLocaleString()} in /{" "}
          {run.tokens_output.toLocaleString()} out tokens
        </span>
        {failed && run.error_message && (
          <span className="text-destructive">{run.error_message}</span>
        )}
      </div>
    </div>
  );
}
