import { ApiError } from "@/lib/api/client";

type ValidationDetail = {
  loc?: unknown[];
  msg?: string;
};

function formatPydanticDetail(detail: ValidationDetail[]): string {
  return detail
    .map((entry) => {
      const path = (entry.loc ?? [])
        .filter((part) => part !== "body")
        .join(".");
      return path ? `${path}: ${entry.msg ?? "Invalid value"}` : (entry.msg ?? "Invalid value");
    })
    .join("\n");
}

export function formatApiError(err: unknown, fallback = "Request failed."): string {
  if (err instanceof ApiError) {
    const detail = (err.body as { detail?: unknown } | undefined)?.detail;

    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }

    if (
      detail &&
      typeof detail === "object" &&
      "issues" in detail &&
      Array.isArray((detail as { issues: unknown }).issues)
    ) {
      return (detail as { issues: string[] }).issues.join("\n");
    }

    if (Array.isArray(detail)) {
      return formatPydanticDetail(detail as ValidationDetail[]);
    }

    return `${fallback} (${err.status})`;
  }

  if (err instanceof Error && err.message) {
    return err.message;
  }

  return fallback;
}

export function toastErrorMessage(err: unknown, fallback: string): string {
  return formatApiError(err, fallback);
}
