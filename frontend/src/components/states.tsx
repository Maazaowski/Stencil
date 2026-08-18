"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { RotateCcw } from "lucide-react";

/**
 * Shared data-surface states.
 *
 * Six screens shipped with a loading branch and no error branch, so a failed
 * request left a permanent skeleton — the UI claimed to be working when it had
 * given up. These three components exist so that cannot happen again: a screen
 * that renders a Loading gets an Error for free.
 *
 * Geometry follows the system: square, hairline-ruled, no shadow, mono labels.
 */

/** Something went wrong fetching. Always says what, and always offers a way out. */
export function ErrorState({
  what,
  onRetry,
  detail,
  className,
}: {
  /** The thing that failed to load, lowercase: "supplier profiles". */
  what: string;
  onRetry?: () => void;
  detail?: string;
  className?: string;
}) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-start gap-2 border border-destructive/40 bg-destructive/8 px-4 py-3.5",
        className,
      )}
    >
      <p className="label-mono text-destructive">Could not load</p>
      <p className="text-sm text-foreground">
        {what.charAt(0).toUpperCase() + what.slice(1)} could not be loaded.
      </p>
      <p className="max-w-prose text-[0.8125rem] text-muted-foreground">
        {detail ?? "The API did not respond. It may be restarting — retrying usually resolves it."}
      </p>
      {onRetry && (
        <Button variant="hairline" size="sm" onClick={onRetry} className="mt-1">
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          Retry
        </Button>
      )}
    </div>
  );
}

/**
 * Nothing here yet. Always names the action that fills it — an empty state that
 * only says "no results" wastes the one moment the user is asking "now what?".
 */
export function EmptyState({
  title,
  hint,
  action,
  className,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-2 border border-dashed border-border-strong px-6 py-10 text-center",
        className,
      )}
    >
      <p className="text-sm font-medium text-foreground">{title}</p>
      {hint && <p className="max-w-prose text-[0.8125rem] text-muted-foreground">{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/**
 * Loading placeholder matching the final geometry.
 *
 * Deliberately NOT pulsing: a pulse implies progress the app cannot actually
 * measure, and thirty-three of them on a page is motion for its own sake.
 */
export function LoadingState({
  rows = 5,
  className,
}: {
  rows?: number;
  className?: string;
}) {
  return (
    <div
      className={cn("border border-border-strong bg-card", className)}
      aria-busy="true"
      aria-live="polite"
    >
      <span className="sr-only">Loading…</span>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-3 border-b border-border px-3 py-2.5 last:border-b-0"
        >
          <div className="h-2.5 w-24 bg-muted" />
          <div className="h-2.5 flex-1 bg-muted/70" />
          <div className="h-2.5 w-16 bg-muted/50" />
        </div>
      ))}
    </div>
  );
}
