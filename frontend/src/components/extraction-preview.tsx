"use client";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { CheckCircle2, AlertTriangle } from "lucide-react";
import type { ExtractionPreview } from "@/types";

function fmtCell(value: string | number | boolean | null): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  return String(value);
}

const TAX_METHOD_LABELS: Record<string, string> = {
  per_line: "printed per-line tax",
  allocated: "invoice tax allocated across rows",
  rate: "calculated from tax rate",
  none: "not delivered",
};

function taxMethodLabel(method: string): string {
  return TAX_METHOD_LABELS[method] ?? method;
}

/**
 * Renders an extracted document's deliverable preview: header summary, the exact
 * delivered columns/rows, and a reconciliation banner. Reused across the processed
 * invoice page, the profile config preview, and the model workbench.
 */
export function ExtractionPreviewView({
  preview,
  hideReconciliation = false,
}: {
  preview: ExtractionPreview;
  /**
   * Suppress the inline reconciliation badge when the caller already shows a
   * dedicated ReconciliationPanel — otherwise the same fact is stated twice on
   * one screen, which is what made the old dashboard read as unreliable.
   */
  hideReconciliation?: boolean;
}) {
  const rec = hideReconciliation ? null : preview.reconciliation;

  return (
    <div className="space-y-4">
      {/* Header fields */}
      {preview.header_fields.length > 0 && (
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3 lg:grid-cols-6">
          {preview.header_fields.map((f) => (
            <div key={f.label} className="space-y-0.5">
              <p className="text-[11px] uppercase tracking-wide text-muted-foreground">
                {f.label}
              </p>
              <p className="text-sm font-medium">{fmtCell(f.value) || "—"}</p>
            </div>
          ))}
        </div>
      )}

      {/* Reconciliation + totals */}
      <div className="flex flex-wrap items-center gap-2">
        {rec && (
          <Badge
            className={
              rec.is_reconciled
                ? "bg-success/12 text-success"
                : "bg-warning/12 text-warning"
            }
          >
            {rec.is_reconciled ? (
              <CheckCircle2 className="mr-1 h-3 w-3" />
            ) : (
              <AlertTriangle className="mr-1 h-3 w-3" />
            )}
            {rec.is_reconciled
              ? "Reconciled"
              : rec.verification_status === "unverifiable" ? "Not verifiable" : "Variance"}
            {rec.variance_pct != null && ` (${(rec.variance_pct * 100).toFixed(2)}%)`}
          </Badge>
        )}
        {preview.totals.map((t) => (
          <span key={t.label} className="text-sm text-muted-foreground">
            {t.label}: <span className="font-medium text-foreground">{fmtCell(t.value)}</span>
          </span>
        ))}
        {preview.tax_method && (
          <span className="text-sm text-muted-foreground">
            Tax method:{" "}
            <span className="font-medium text-foreground">
              {taxMethodLabel(preview.tax_method)}
            </span>
          </span>
        )}
        <span className="ml-auto text-xs text-muted-foreground">
          {preview.row_count} delivered row{preview.row_count === 1 ? "" : "s"}
        </span>
      </div>

      {/* Deliverable table */}
      <div className="overflow-x-auto rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              {preview.columns.map((c, i) => (
                <TableHead key={`${c.header}-${i}`} className="whitespace-nowrap">
                  {c.header}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {preview.rows.length ? (
              preview.rows.map((row, ri) => (
                <TableRow key={ri}>
                  {row.map((cell, ci) => (
                    <TableCell key={ci} className="whitespace-nowrap">
                      {fmtCell(cell)}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell
                  colSpan={Math.max(preview.columns.length, 1)}
                  className="h-20 text-center text-muted-foreground"
                >
                  No line items extracted.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      {preview.conflicts && preview.conflicts.length > 0 && (
        <div className="rounded-md border border-warning/30 bg-warning/12 px-3 py-2 text-xs text-warning">
          <p className="font-medium">
            Setup conflicts — a note contradicts a configured field
          </p>
          <ul className="list-disc space-y-1 pl-4">
            {preview.conflicts.map((c, i) => (
              <li key={i}>
                <span className="font-mono">{c.field}</span>
                {" = "}
                <span className="font-medium">{c.structured_value}</span>: {c.message}
                {c.resolution === "note_override" && (
                  <span className="font-medium"> (note overrides this field)</span>
                )}
                {c.note_fragment && (
                  <p className="mt-0.5 italic opacity-80">“{c.note_fragment}”</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {preview.warnings && preview.warnings.length > 0 && (
        <div className="rounded-md border border-warning/30 bg-warning/12 px-3 py-2 text-xs text-warning">
          <p className="font-medium">Warnings</p>
          <ul className="list-disc pl-4">
            {preview.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
