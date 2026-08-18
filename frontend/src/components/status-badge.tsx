"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { IntakeStatus, ExtractionPath, ModelStatus } from "@/types";

/**
 * Status marks.
 *
 * The old version hardcoded eleven raw Tailwind palettes (gray/blue/green/amber/
 * red/purple/indigo/orange/yellow/cyan/pink), bypassing the token system
 * entirely — which is why the theme change alone could not fix the invoice
 * table. Everything here now resolves through semantic tokens.
 *
 * The governing rule: **normal is silent.** A healthy invoice gets no mark at
 * all. Previously 18 of 20 rows carried an amber chip, so amber had stopped
 * meaning "look at this". Marks are for deviation only.
 *
 * Accessibility: every mark carries a word, never colour alone, so the table
 * survives greyscale.
 */

// ── Intake / invoice status ────────────────────────────────

export function StatusBadge({ status }: { status: IntakeStatus | string }) {
  // Silence is the healthy state. Completed rows carry no mark.
  if (status === "completed") {
    return (
      <span className="text-muted-foreground/70" title="Completed">
        —
      </span>
    );
  }

  switch (status) {
    case "completed_with_warnings":
      return <Badge variant="warning">Variance</Badge>;
    case "failed":
      return <Badge variant="destructive">Failed</Badge>;
    case "processing":
      return <Badge variant="cut">Running</Badge>;
    default:
      return <Badge variant="neutral">{String(status).replace(/_/g, " ")}</Badge>;
  }
}

// ── Extraction path ────────────────────────────────────────

/**
 * The product's proudest state is `model` — the invoice that cost nothing.
 * It gets the accent. `ai` is the expensive fallback and is deliberately quiet;
 * it used to be a purple chip, which advertised the least differentiated part
 * of the system.
 */
export function ExtractionPathBadge({ path }: { path: ExtractionPath | string }) {
  switch (path) {
    case "model":
      return <Badge variant="cut" title="Ran from the saved template at no AI cost">Free</Badge>;
    case "model_fallback_ai":
      return <Badge variant="warning">Fell back</Badge>;
    case "ai":
      return <Badge variant="neutral">Paid</Badge>;
    default:
      return <Badge variant="neutral">{String(path)}</Badge>;
  }
}

// ── Model lifecycle ────────────────────────────────────────

export function ModelStatusBadge({ status }: { status: ModelStatus | string }) {
  switch (status) {
    case "approved":
      return <Badge variant="success">Ready</Badge>;
    case "candidate":
      return <Badge variant="cut">Cutting</Badge>;
    case "failed_validation":
      return <Badge variant="destructive">Failed</Badge>;
    case "retired":
      return <Badge variant="ghost">Retired</Badge>;
    default:
      return <Badge variant="neutral">{String(status).replace(/_/g, " ")}</Badge>;
  }
}

// ── Reconciliation — the signal that actually predicts a bad extraction ────

/**
 * Replaces the Confidence column. Confidence is stamped as a hardcoded 1.0 by
 * the default extraction mode, and 40% of jobs reporting =99% failed this
 * check — so confidence was decoration and this is measurement.
 */
export function ReconciliationBadge({
  isReconciled,
  variance,
}: {
  isReconciled?: boolean | null;
  variance?: number | null;
}) {
  if (isReconciled === null || isReconciled === undefined) {
    return <span className="text-muted-foreground/70">—</span>;
  }
  if (isReconciled) {
    return <span className="font-mono text-xs text-muted-foreground">balanced</span>;
  }
  return (
    <span
      className="font-mono text-xs font-medium text-warning"
      title="Line items do not sum to the invoice total"
    >
      {variance != null
        ? `${variance > 0 ? "+" : ""}${variance.toLocaleString(undefined, {
            maximumFractionDigits: 2,
          })}`
        : "off"}
    </span>
  );
}

// ── Confidence (deprecated) ────────────────────────────────

/**
 * @deprecated The default extraction path hardcodes this to 1.0, so it measures
 * nothing. Kept only so un-migrated screens compile; use ReconciliationBadge.
 * Rendered deliberately quiet so it stops reading as a quality signal.
 */
export function ConfidenceBadge({ confidence }: { confidence: number }) {
  return (
    <span className="font-mono text-xs text-muted-foreground">
      {(confidence * 100).toFixed(0)}%
    </span>
  );
}

// ── Charge type — structural, not semantic ─────────────────

/**
 * Nine colours became one. Charge type is a category label, not a status; it
 * carries no urgency and should not compete with the marks that do. Tax and
 * credit keep a tint because they genuinely change how a line reads.
 */
const chargeTone: Record<string, "neutral" | "success" | "warning"> = {
  credit: "success",
  tax: "warning",
};

export function ChargeTypeBadge({ type }: { type: string }) {
  return (
    <Badge variant={chargeTone[type] ?? "neutral"} className={cn("normal-case")}>
      {type.replace(/_/g, " ")}
    </Badge>
  );
}
