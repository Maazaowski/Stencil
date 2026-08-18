"use client";

import Link from "next/link";
import { useDashboardStats } from "@/hooks/use-dashboard";
import { cn } from "@/lib/utils";

/**
 * The status line — one ruled strip that replaces seven stat cards.
 *
 * It answers the only question an operator has on arrival: *what needs me
 * today?* Three figures, in priority order, each a link to the filtered view
 * that acts on it. Everything else that used to be here (cost, volume, monthly
 * trend) moved to /insights, because it is a monthly question asked by a
 * different person.
 *
 * Deliberately not cards: cards imply peers, and these are not peers. "Needs
 * review" is the one that costs someone their afternoon.
 */
function Figure({
  value,
  label,
  tone = "default",
  href,
  title,
}: {
  value: string;
  label: string;
  tone?: "default" | "alert" | "cut";
  href?: string;
  title?: string;
}) {
  const body = (
    <span className="flex items-baseline gap-2" title={title}>
      <span
        className={cn(
          "font-mono text-lg leading-none font-semibold [font-variant-numeric:tabular-nums]",
          tone === "alert" && "text-warning",
          tone === "cut" && "text-primary",
        )}
      >
        {value}
      </span>
      <span className="label-mono">{label}</span>
    </span>
  );

  if (!href) return body;
  return (
    <Link
      href={href}
      className="rounded-sm transition-colors hover:bg-accent focus-visible:bg-accent"
    >
      {body}
    </Link>
  );
}

export function WorkStatusLine({ className }: { className?: string }) {
  const { data: stats, isLoading, isError } = useDashboardStats();

  // A broken stats call must not take the queue down with it — the queue is the
  // page, this is a header. Fail quiet.
  if (isError) return null;

  if (isLoading || !stats) {
    return (
      <div
        className={cn("flex h-9 items-center gap-6 border-y border-border px-3", className)}
        aria-busy="true"
      >
        <span className="h-2.5 w-28 bg-muted" />
        <span className="h-2.5 w-24 bg-muted/70" />
        <span className="h-2.5 w-20 bg-muted/50" />
      </div>
    );
  }

  const free = stats.model_path_count ?? 0;
  const scored = stats.scored_jobs ?? 0;

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-7 gap-y-2 border-y border-border px-3 py-2",
        className,
      )}
    >
      <Figure
        value={String(stats.pending_exceptions ?? 0)}
        label="need you"
        tone={(stats.pending_exceptions ?? 0) > 0 ? "alert" : "default"}
        href="/exceptions"
        title="Failed, plus delivered with a variance"
      />
      <Figure
        value={`${(stats.success_rate ?? 0).toFixed(0)}%`}
        label="reconciled"
        title={`${stats.reconciled_jobs ?? 0} of ${scored} extractions balance to the invoice total`}
      />
      <Figure
        value={String(free)}
        label="ran free"
        tone={free > 0 ? "cut" : "default"}
        href="/invoices?path=model"
        title="Extracted from a saved template at no AI cost"
      />

      {/* The product's whole thesis, stated when it is not yet true. */}
      {free === 0 && (
        <span className="text-[0.8125rem] text-muted-foreground">
          No invoice has run from a template yet — every one is paying AI cost.
        </span>
      )}
    </div>
  );
}
