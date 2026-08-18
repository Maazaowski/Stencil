"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import { cn } from "@/lib/utils";
import { pipelineWsBase } from "@/lib/ws";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
  Circle,
  FileText,
  Download,
  Ban,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import {
  safeFormatDate,
  formatDurationMs,
  parseApiDate,
} from "@/lib/format-date";
import { resolveXlsxOutputFilename } from "@/lib/output-naming";
import type { IntakeDetail, IntakeStatus, ProcessingLog } from "@/types";

// ── Pipeline checklist steps ───────────────────────────────

export const PIPELINE_STEPS = [
  { key: "intake", label: "Intake", description: "Registering and archiving PDF" },
  { key: "fingerprint", label: "Fingerprint", description: "Analyzing PDF layout structure" },
  { key: "routing", label: "Routing", description: "Selecting supplier profile" },
  { key: "classification", label: "Classification", description: "Identifying supplier" },
  { key: "ai_extraction", label: "AI Extraction", description: "Extracting invoice data via AI" },
  { key: "model_extraction", label: "Model Extraction", description: "Extracting via approved model" },
  { key: "model_training", label: "Model Training", description: "Training pipeline orchestration" },
  { key: "model_authoring", label: "Model Authoring", description: "AI authoring extraction rules" },
  { key: "model_validation", label: "Model Validation", description: "Validating model against AI baseline" },
  { key: "extraction", label: "Extraction", description: "Extracting invoice data" },
  { key: "reconciliation", label: "Reconciliation", description: "Verifying totals" },
  { key: "output", label: "Output", description: "Generating XLSX and JSON" },
] as const;

type StepStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "skipped"
  | "low_confidence";

interface StepState {
  status: StepStatus;
  message: string;
  durationMs?: number | null;
}

const OPTIONAL_STEP_KEYS = new Set([
  "ai_extraction",
  "model_extraction",
  "model_training",
  "model_authoring",
  "model_validation",
  "extraction",
]);

const REVIEW_STEPS = new Set([
  "model_review",
  "model_generation",
  "model_validation",
]);

function normalizeLogStatus(status: string): StepStatus {
  if (status === "completed" || status === "success" || status === "matched" || status === "passed")
    return "completed";
  if (status === "failed" || status === "error" || status === "mismatch")
    return "failed";
  if (status === "running" || status === "in_progress" || status === "started")
    return "running";
  if (status === "skipped") return "skipped";
  if (status === "low_confidence") return "low_confidence";
  return "pending";
}

export function getStepStatusFromLogs(
  stepKey: string,
  logs: Pick<
    ProcessingLog,
    "step" | "status" | "message" | "duration_ms" | "details"
  >[],
  wsSteps: Record<string, StepState> = {},
): { status: StepStatus; message: string; durationMs?: number | null } {
  const matching = logs.filter((l) => l.step === stepKey);
  const last = matching.length > 0 ? matching[matching.length - 1] : null;

  const durationMs =
    last?.duration_ms ??
    (typeof last?.details?.duration_ms === "number"
      ? (last.details.duration_ms as number)
      : null);

  const dbStatus = last ? normalizeLogStatus(last.status) : "pending";
  const dbMessage = last?.message ?? "";

  if (
    dbStatus === "completed" ||
    dbStatus === "failed" ||
    dbStatus === "skipped" ||
    dbStatus === "low_confidence"
  ) {
    return { status: dbStatus, message: dbMessage, durationMs };
  }

  if (wsSteps[stepKey]) {
    return wsSteps[stepKey];
  }

  if (matching.length === 0) {
    return { status: "pending", message: "" };
  }

  return { status: dbStatus, message: dbMessage, durationMs };
}

function isTerminalStepStatus(status: StepStatus): boolean {
  return (
    status === "completed" ||
    status === "failed" ||
    status === "skipped" ||
    status === "low_confidence"
  );
}

/** First in-flight step while the intake is still processing. */
export function inferRunningStepKey(
  logs: Pick<ProcessingLog, "step" | "status" | "message" | "duration_ms" | "details">[],
  wsSteps: Record<string, StepState>,
  isProcessing: boolean,
): string | null {
  if (!isProcessing) return null;

  for (const [key, state] of Object.entries(wsSteps)) {
    if (state.status === "running") return key;
  }

  for (const step of PIPELINE_STEPS) {
    const { status } = getStepStatusFromLogs(step.key, logs, {});
    if (status === "running") return step.key;
  }

  for (let i = 0; i < PIPELINE_STEPS.length; i++) {
    const step = PIPELINE_STEPS[i];
    const previousDone = PIPELINE_STEPS.slice(0, i).every((prev) => {
      const prevStatus = getStepStatusFromLogs(prev.key, logs, wsSteps).status;
      return isTerminalStepStatus(prevStatus);
    });
    if (!previousDone) continue;

    const { status } = getStepStatusFromLogs(step.key, logs, wsSteps);
    if (status === "running" || status === "pending") return step.key;
    if (status === "failed") return null;
  }

  return null;
}

/** Audit rows stay historical, but a later terminal log for the same step closes out spinners. */
export function effectiveAuditLogStatus(
  log: Pick<ProcessingLog, "step" | "status" | "timestamp">,
  allLogs: Pick<ProcessingLog, "step" | "status" | "timestamp">[],
  pipelineTerminal: boolean,
): StepStatus {
  const raw = normalizeLogStatus(log.status);
  if (raw !== "running") return raw;

  const logTime = parseApiDate(log.timestamp)?.getTime() ?? 0;
  const later = allLogs.filter(
    (entry) =>
      entry.step === log.step &&
      (parseApiDate(entry.timestamp)?.getTime() ?? 0) > logTime,
  );

  for (const entry of later) {
    const laterStatus = normalizeLogStatus(entry.status);
    if (isTerminalStepStatus(laterStatus)) {
      return laterStatus === "failed" ? "failed" : "completed";
    }
  }

  if (pipelineTerminal) {
    return "completed";
  }

  return raw;
}

function computeElapsedMs(
  logs: ProcessingLog[],
  nowMs: number,
  isProcessing: boolean,
): number | null {
  if (!logs.length) return null;
  const sorted = [...logs].sort(
    (a, b) =>
      (parseApiDate(a.timestamp)?.getTime() ?? 0) -
      (parseApiDate(b.timestamp)?.getTime() ?? 0),
  );
  const start = parseApiDate(sorted[0].timestamp)?.getTime();
  if (start == null) return null;

  if (isProcessing) {
    return Math.max(0, nowMs - start);
  }

  const end = parseApiDate(sorted[sorted.length - 1].timestamp)?.getTime();
  if (end == null) return null;
  return Math.max(0, end - start);
}

function StepIcon({ status }: { status: StepStatus }) {
  switch (status) {
    case "completed":
      return <CheckCircle2 className="h-5 w-5 text-success" />;
    case "failed":
      return <XCircle className="h-5 w-5 text-destructive" />;
    case "running":
      return <Loader2 className="h-5 w-5 text-primary animate-spin" />;
    case "low_confidence":
      return <Circle className="h-5 w-5 text-warning" />;
    case "skipped":
      return <Circle className="h-5 w-5 text-muted-foreground/40" />;
    default:
      return <Circle className="h-5 w-5 text-muted-foreground/40" />;
  }
}

function formatStep(step: string): string {
  return step
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function metadataValue(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  return String(value);
}

function ProfileMetadata({ details }: { details: ProcessingLog["details"] }) {
  if (!details) return null;
  const profileId = metadataValue(details.profile_id);
  const profileVersion = metadataValue(details.profile_version);
  const profileStatus = metadataValue(details.profile_status);
  const profileSource = metadataValue(details.profile_source);
  if (!profileId && !profileSource) return null;
  return (
    <div className="mt-1 flex flex-wrap gap-1.5 text-[11px] text-muted-foreground">
      {profileId && <span>Profile {profileId}</span>}
      {profileVersion && <span>v{profileVersion}</span>}
      {profileStatus && <span>{profileStatus}</span>}
      {profileSource && <span>{profileSource}</span>}
    </div>
  );
}

function reviewOutcome(log: ProcessingLog): "failed" | "comparison" | "success" | null {
  const outcome = log.details?.outcome;
  if (outcome === "failed" || outcome === "comparison" || outcome === "success") {
    return outcome;
  }
  if (log.step === "model_generation" && log.status === "failed") return "failed";
  if (log.step === "model_validation" && log.status === "failed") return "failed";
  if (log.step === "model_review" && log.status === "completed") return "comparison";
  return null;
}

function ModelReviewDownloads({
  intakeId,
  log,
}: {
  intakeId: string;
  log: ProcessingLog;
}) {
  const outcome = reviewOutcome(log);
  if (!REVIEW_STEPS.has(log.step) && outcome === null) return null;
  if (log.step !== "model_review" && log.step !== "model_generation" && log.step !== "model_validation") {
    return null;
  }

  const files = (log.details?.files as string[] | undefined) ?? [];
  const hasReviewFiles =
    files.length > 0 ||
    log.step === "model_review" ||
    log.step === "model_generation";

  if (!hasReviewFiles && outcome !== "failed") return null;

  const tone =
    outcome === "failed"
      ? "border-destructive/30 bg-destructive/12"
      : outcome === "success"
        ? "border-success/30 bg-success/12"
        : "border-border bg-muted/30";

  return (
    <div className={cn("mt-2 rounded-md border p-2", tone)}>
      <p className="text-xs text-muted-foreground mb-2">
        {outcome === "failed"
          ? "Model training review — execution or authoring failed"
          : outcome === "success"
            ? "Model training review — candidate matched ground truth"
            : "Model comparison files"}
      </p>
      <div className="flex flex-wrap gap-1.5">
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          render={
            <a
              href={`/api/v1/invoices/${intakeId}/model-review/ai_output.xlsx`}
              download="ai_output.xlsx"
            />
          }
        >
          <Download className="h-3 w-3 mr-1" />
          AI XLSX
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          render={
            <a
              href={`/api/v1/invoices/${intakeId}/model-review/model_output.xlsx`}
              download="model_output.xlsx"
            />
          }
        >
          <Download className="h-3 w-3 mr-1" />
          Model XLSX
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          render={
            <a
              href={`/api/v1/invoices/${intakeId}/model-review/training_report.json`}
              download="training_report.json"
            />
          }
        >
          <Download className="h-3 w-3 mr-1" />
          Diff JSON
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          render={
            <a
              href={`/api/v1/invoices/${intakeId}/model-review/execution_error.txt`}
              download="execution_error.txt"
            />
          }
        >
          <Download className="h-3 w-3 mr-1" />
          Error log
        </Button>
      </div>
    </div>
  );
}

function formatStepLabel(stepKey: string): string {
  const known = PIPELINE_STEPS.find((step) => step.key === stepKey);
  return known?.label ?? formatStep(stepKey);
}

function headerLabel(status: IntakeStatus | string, isProcessing: boolean): string {
  if (isProcessing) return "Processing…";
  if (status === "completed") return "Complete";
  if (status === "completed_with_warnings") return "Complete with warnings";
  if (status === "failed") return "Failed";
  if (status === "received") return "Queued";
  return String(status);
}

export interface ProcessingTimelineProps {
  /** When set, fetches live invoice data (polling + WebSocket). */
  intakeId?: string;
  /** Static logs when not using intakeId fetch. */
  logs?: ProcessingLog[];
  status?: IntakeStatus | string;
  filename?: string;
  variant?: "card" | "inline";
  showCancel?: boolean;
  showFullLog?: boolean;
}

export function ProcessingTimeline({
  intakeId,
  logs: logsProp,
  status: statusProp,
  filename: filenameProp,
  variant = "inline",
  showCancel = true,
  showFullLog = true,
}: ProcessingTimelineProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const confirm = useConfirm();
  const [wsSteps, setWsSteps] = useState<Record<string, StepState>>({});
  const [fullLogOpen, setFullLogOpen] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const wsRef = useRef<WebSocket | null>(null);

  const cancelInvoice = useMutation({
    mutationFn: () => api.post(`/invoices/${intakeId}/cancel`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["invoices", intakeId] });
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
  });

  useEffect(() => {
    if (!intakeId) return;
    const wsUrl = `${pipelineWsBase()}/api/v1/pipeline/live/${intakeId}`;
    try {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.step && data.step !== "pipeline") {
            const durationMs =
              typeof data.details?.duration_ms === "number"
                ? data.details.duration_ms
                : null;
            setWsSteps((prev) => ({
              ...prev,
              [data.step]: {
                status: normalizeLogStatus(data.status),
                message: data.message ?? "",
                durationMs,
              },
            }));
          }
          if (
            data.step === "pipeline" &&
            (data.status === "completed" || data.status === "failed")
          ) {
            queryClient.invalidateQueries({ queryKey: ["invoices"] });
            queryClient.invalidateQueries({ queryKey: ["invoices", intakeId] });
            queryClient.invalidateQueries({ queryKey: ["dashboard"] });
          }
        } catch {
          /* ignore */
        }
      };
      return () => {
        ws.close();
        wsRef.current = null;
      };
    } catch {
      /* polling fallback */
    }
  }, [intakeId, queryClient]);

  const { data: fetchedInvoice } = useQuery({
    queryKey: ["invoices", intakeId],
    queryFn: () => api.get<IntakeDetail>(`/invoices/${intakeId}`),
    enabled: !!intakeId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (
        status === "completed" ||
        status === "completed_with_warnings" ||
        status === "failed"
      ) {
        return false;
      }
      return 3000;
    },
  });

  useEffect(() => {
    const status = fetchedInvoice?.status;
    if (
      status === "completed" ||
      status === "completed_with_warnings" ||
      status === "failed"
    ) {
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    }
  }, [fetchedInvoice?.status, queryClient]);

  const invoice = intakeId ? fetchedInvoice : undefined;
  const logs = invoice?.logs ?? logsProp ?? [];
  const status = invoice?.status ?? statusProp ?? "received";
  const filename = invoice?.original_filename ?? filenameProp;
  const xlsxFilename = resolveXlsxOutputFilename(
    invoice?.jobs?.[0]?.xlsx_output_filename,
    filename,
  );
  const jobCompletedAt = invoice?.jobs?.[0]?.completed_at ?? null;

  const isComplete =
    status === "completed" || status === "completed_with_warnings";
  const isFailed = status === "failed";
  const actuallyProcessing = status === "processing" || status === "received";

  useEffect(() => {
    if (!actuallyProcessing) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [actuallyProcessing]);

  const pipelineTerminal = isComplete || isFailed;
  const activeWsSteps = pipelineTerminal ? {} : wsSteps;

  const runningStepKey = inferRunningStepKey(logs, activeWsSteps, actuallyProcessing);

  const visibleSteps = PIPELINE_STEPS.filter((step) => {
    if (OPTIONAL_STEP_KEYS.has(step.key)) {
      const s = getStepStatusFromLogs(step.key, logs, activeWsSteps);
      return s.status !== "pending" || step.key === runningStepKey;
    }
    return true;
  });

  const elapsedMs = computeElapsedMs(logs, nowMs, actuallyProcessing);
  const sortedAudit = [...logs].sort(
    (a, b) =>
      (parseApiDate(a.timestamp)?.getTime() ?? 0) -
      (parseApiDate(b.timestamp)?.getTime() ?? 0),
  );

  const timelineBody = (
    <>
      <div className="flex items-center justify-between gap-3 mb-4 text-sm">
        <div className="flex items-center gap-2 text-muted-foreground">
          {actuallyProcessing && (
            <Loader2 className="h-4 w-4 animate-spin text-primary" />
          )}
          <span className="font-medium text-foreground">
            {headerLabel(status, actuallyProcessing)}
          </span>
          {elapsedMs != null && (
            <span className="text-xs">
              · {formatDurationMs(elapsedMs)} elapsed
            </span>
          )}
          {runningStepKey && actuallyProcessing && (
            <span className="text-xs text-primary">
              · {formatStepLabel(runningStepKey)}
            </span>
          )}
        </div>
        {actuallyProcessing && showCancel && intakeId && (
          <Button
            variant="outline"
            size="sm"
            disabled={cancelInvoice.isPending}
            onClick={async () => {
              const ok = await confirm({
                title: "Cancel processing for this invoice?",
                confirmLabel: "Cancel processing",
                variant: "destructive",
              });
              if (ok) cancelInvoice.mutate();
            }}
          >
            <Ban className="h-4 w-4 mr-1" />
            Cancel
          </Button>
        )}
      </div>

      <div className="space-y-0">
        {visibleSteps.map((step, i) => {
          const { status: stepStatus, message, durationMs } =
            getStepStatusFromLogs(step.key, logs, activeWsSteps);
          let effectiveStatus = stepStatus;
          if (
            actuallyProcessing &&
            step.key === runningStepKey &&
            (stepStatus === "pending" || stepStatus === "running")
          ) {
            effectiveStatus = "running";
          } else if (pipelineTerminal && stepStatus === "running") {
            effectiveStatus = "completed";
          }
          const durationLabel = formatDurationMs(durationMs);
          const isLast = i === visibleSteps.length - 1;

          return (
            <div key={step.key} className="flex gap-3">
              <div className="flex flex-col items-center">
                <StepIcon status={effectiveStatus} />
                {!isLast && (
                  <div
                    className={cn(
                      "w-px flex-1 min-h-5",
                      effectiveStatus === "completed" ? "bg-success/12" : "bg-border",
                    )}
                  />
                )}
              </div>
              <div className={cn("pb-3", isLast && "pb-0")}>
                <span
                  className={cn(
                    "text-sm",
                    effectiveStatus === "completed" && "font-medium text-foreground",
                    effectiveStatus === "running" && "font-medium text-primary",
                    effectiveStatus === "failed" && "font-medium text-destructive",
                    effectiveStatus === "pending" && "text-muted-foreground",
                  )}
                >
                  {step.label}
                  {durationLabel ? ` · ${durationLabel}` : ""}
                </span>
                {(effectiveStatus === "running" ||
                  effectiveStatus === "failed" ||
                  message) && (
                  <p
                    className={cn(
                      "text-xs mt-0.5",
                      effectiveStatus === "failed"
                        ? "text-destructive"
                        : "text-muted-foreground",
                    )}
                  >
                    {message || step.description}
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {isComplete && intakeId && (
        <div
          className={cn(
            "mt-4 flex items-center gap-2 rounded-lg border p-3",
            status === "completed_with_warnings"
              ? "border-warning/30 bg-warning/12"
              : "border-success/30 bg-success/12",
          )}
        >
          {status === "completed_with_warnings" ? (
            <Clock className="h-4 w-4 text-warning shrink-0" />
          ) : (
            <CheckCircle2 className="h-4 w-4 text-success shrink-0" />
          )}
          <span
            className={cn(
              "text-sm flex-1",
              status === "completed_with_warnings"
                ? "text-warning"
                : "text-success",
            )}
          >
            {status === "completed_with_warnings"
              ? "Delivered with warnings"
              : "Extraction complete"}
            {jobCompletedAt && (
              <span className="block text-xs font-normal opacity-80 mt-0.5">
                Completed {safeFormatDate(jobCompletedAt, "MMM d, yyyy HH:mm")}
              </span>
            )}
          </span>
          <div className="flex gap-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
              render={
                <a
                  href={`/api/v1/invoices/${intakeId}/output/${encodeURIComponent(xlsxFilename)}`}
                  download={xlsxFilename}
                />
              }
            >
              <Download className="h-3 w-3" />
              XLSX
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
              render={
                <a
                  href={`/api/v1/invoices/${intakeId}/output/canonical_invoice.json`}
                  download
                />
              }
            >
              <Download className="h-3 w-3" />
              JSON
            </Button>
            {variant === "card" && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                onClick={() => router.push(`/invoices/${intakeId}`)}
              >
                Details
              </Button>
            )}
          </div>
        </div>
      )}

      {isFailed && intakeId && (
        <div className="mt-4 flex items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/12 p-3">
          <XCircle className="h-4 w-4 text-destructive shrink-0" />
          <span className="text-sm text-destructive flex-1">
            Processing failed
            {jobCompletedAt && (
              <span className="block text-xs font-normal opacity-80 mt-0.5">
                Failed {safeFormatDate(jobCompletedAt, "MMM d, yyyy HH:mm")}
              </span>
            )}
          </span>
          {variant === "card" && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
              onClick={() => router.push(`/invoices/${intakeId}`)}
            >
              Details
            </Button>
          )}
        </div>
      )}

      {showFullLog && sortedAudit.length > 0 && (
        <div className="mt-4 border-t pt-4">
          <button
            type="button"
            className="flex w-full items-center justify-between text-sm font-medium text-muted-foreground hover:text-foreground"
            onClick={() => setFullLogOpen((v) => !v)}
          >
            Full processing log ({sortedAudit.length} entries)
            {fullLogOpen ? (
              <ChevronUp className="h-4 w-4" />
            ) : (
              <ChevronDown className="h-4 w-4" />
            )}
          </button>
          {fullLogOpen && (
            <div className="mt-3 space-y-0">
              {sortedAudit.map((log, i) => {
                const iconStatus = effectiveAuditLogStatus(
                  log,
                  sortedAudit,
                  pipelineTerminal,
                );
                const isLast = i === sortedAudit.length - 1;
                const durationMs =
                  log.duration_ms ??
                  (typeof log.details?.duration_ms === "number"
                    ? (log.details.duration_ms as number)
                    : null);

                return (
                  <div key={log.id} className="flex gap-3">
                    <div className="flex flex-col items-center">
                      <StepIcon status={iconStatus} />
                      {!isLast && (
                        <div className="w-px flex-1 bg-border min-h-4" />
                      )}
                    </div>
                    <div className={cn("pb-3", isLast && "pb-0")}>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-medium">
                          {formatStep(log.step)}
                        </span>
                        {formatDurationMs(durationMs) && (
                          <span className="text-xs text-muted-foreground">
                            {formatDurationMs(durationMs)}
                          </span>
                        )}
                        <span className="text-xs text-muted-foreground">
                          {safeFormatDate(log.timestamp, "HH:mm:ss")}
                        </span>
                      </div>
                      {log.message && (
                        <p className="text-xs text-muted-foreground mt-0.5">
                          {log.message}
                        </p>
                      )}
                      <ProfileMetadata details={log.details} />
                      {intakeId && (
                        <ModelReviewDownloads intakeId={intakeId} log={log} />
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {!logs.length && !intakeId && (
        <p className="text-sm text-muted-foreground">No processing logs yet.</p>
      )}
    </>
  );

  if (variant === "card") {
    if (intakeId && !invoice) return null;
    return (
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-muted-foreground" />
            {filename && (
              <CardTitle className="text-sm font-medium">{filename}</CardTitle>
            )}
          </div>
        </CardHeader>
        <CardContent>{timelineBody}</CardContent>
      </Card>
    );
  }

  if (intakeId && !invoice) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground py-4">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading pipeline status…
      </div>
    );
  }

  return timelineBody;
}
