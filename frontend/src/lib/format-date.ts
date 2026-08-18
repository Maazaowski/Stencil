import { format } from "date-fns";

/** Parse API datetime strings, treating naive ISO values as UTC. */
export function parseApiDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const normalized =
    value.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Format an ISO date string in the browser's local timezone. */
export function safeFormatDate(
  value: string | null | undefined,
  pattern: string,
  fallback = "—"
): string {
  const date = parseApiDate(value);
  if (!date) return fallback;
  try {
    return format(date, pattern);
  } catch {
    return fallback;
  }
}

export function formatDurationMs(ms: number | null | undefined): string | null {
  if (ms == null || ms <= 0) return null;
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
