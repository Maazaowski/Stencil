"use client";

import { cn } from "@/lib/utils";
import type { PreviewReconciliation } from "@/types";

/**
 * The reconciliation panel.
 *
 * The operator's task on the invoice detail screen is exactly one thing: **find
 * the row that is wrong.** The page used to open with a processing log and make
 * them hunt for it.
 *
 * So this shows the arithmetic, explicitly, the way you would check it on paper:
 * what the line items add up to, what the invoice claims, and the difference.
 * When those agree it collapses to a single quiet line — a balanced invoice
 * needs no explanation.
 */
function Money({
  value,
  className,
  sign = false,
}: {
  value: number | null | undefined;
  className?: string;
  sign?: boolean;
}) {
  if (value === null || value === undefined) {
    return <span className="text-muted-foreground">—</span>;
  }
  const formatted = Math.abs(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const prefix = sign ? (value > 0 ? "+" : value < 0 ? "−" : "") : value < 0 ? "−" : "";
  return (
    <span className={cn("font-mono [font-variant-numeric:tabular-nums]", className)}>
      {prefix}
      {formatted}
    </span>
  );
}

export function ReconciliationPanel({
  reconciliation,
  lineItemCount,
  className,
}: {
  reconciliation?: PreviewReconciliation | null;
  lineItemCount?: number;
  className?: string;
}) {
  if (!reconciliation) return null;

  const { is_reconciled, line_items_sum, reconcile_target, stated_total, variance } =
    reconciliation;
  const target = reconcile_target ?? stated_total;

  // Balanced: one line, no ceremony.
  if (is_reconciled) {
    return (
      <p
        className={cn("flex items-center gap-2 text-[0.8125rem] text-muted-foreground", className)}
      >
        <span className="inline-block h-1.5 w-1.5 bg-success" aria-hidden="true" />
        {lineItemCount ?? 0} line item{lineItemCount === 1 ? "" : "s"} balance to the invoice
        total.
      </p>
    );
  }

  return (
    <div className={cn("border border-warning/40 bg-warning/8", className)}>
      <div className="border-b border-warning/25 px-3 py-2">
        <p className="label-mono text-warning">Does not reconcile</p>
        <p className="mt-0.5 text-[0.8125rem] text-foreground">
          The extracted rows do not add up to what the invoice says. One or more line items
          is wrong, missing, or double-counted.
        </p>
      </div>

      {/* The arithmetic, as you would check it on paper. */}
      <dl className="px-3 py-2.5 text-[0.8125rem]">
        <div className="flex items-baseline justify-between gap-6 py-0.5">
          <dt className="text-muted-foreground">
            {lineItemCount ?? 0} line item{lineItemCount === 1 ? "" : "s"} sum to
          </dt>
          <dd>
            <Money value={line_items_sum} />
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-6 py-0.5">
          <dt className="text-muted-foreground">Invoice states</dt>
          <dd>
            <Money value={target} />
          </dd>
        </div>
        <div className="mt-1 flex items-baseline justify-between gap-6 border-t border-warning/25 pt-1.5">
          <dt className="font-medium">Off by</dt>
          <dd>
            <Money value={variance} sign className="font-semibold text-warning" />
          </dd>
        </div>
      </dl>
    </div>
  );
}
