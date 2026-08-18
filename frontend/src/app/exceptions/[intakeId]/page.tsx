"use client";

import { use, useState } from "react";
import { useRouter } from "next/navigation";
import {
  useException,
  useRetryException,
  useResolveException,
} from "@/hooks/use-exceptions";
import { useInvoice } from "@/hooks/use-invoices";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { usePrompt } from "@/components/ui/confirm-dialog";
import { toast } from "sonner";
import { format } from "date-fns";
import Link from "next/link";
import {
  ArrowLeft,
  AlertTriangle,
  RefreshCw,
  CheckCircle2,
  FileDown,
  FileText,
  Clock,
  Info,
  ChevronDown,
  ChevronRight,
  Plus,
} from "lucide-react";

// ── Color helpers ───────────────────────────────────────────

const reasonColors: Record<string, string> = {
  low_confidence: "bg-warning/12 text-warning border-warning/30",
  reconciliation_failure: "bg-destructive/12 text-destructive border-destructive/30",
  schema_validation_failure: "bg-destructive/12 text-destructive border-destructive/30",
  extraction_error: "bg-destructive/12 text-destructive border-destructive/30",
  unknown_supplier: "bg-muted text-muted-foreground border-border-foreground",
  unreadable_pdf: "bg-muted text-muted-foreground border-border-foreground",
  no_line_items: "bg-warning/12 text-warning border-warning/30",
};

const stepStatusColors: Record<string, string> = {
  completed: "text-success",
  failed: "text-destructive",
  low_confidence: "text-warning",
  running: "text-primary",
};

function formatReason(reason: string): string {
  return reason
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// ── Page Component ──────────────────────────────────────────

export default function ExceptionDetailPage({
  params,
}: {
  params: Promise<{ intakeId: string }>;
}) {
  const { intakeId } = use(params);
  const router = useRouter();
  const { data: exception, isLoading, isError } = useException(intakeId);
  const { data: invoice } = useInvoice(intakeId);
  const retryException = useRetryException();
  const resolveException = useResolveException();
  const prompt = usePrompt();
  const [showPartial, setShowPartial] = useState(false);
  const [partialData, setPartialData] = useState<Record<string, unknown> | null>(null);
  const [loadingPartial, setLoadingPartial] = useState(false);

  async function handleRetry() {
    try {
      await retryException.mutateAsync(intakeId);
      toast.success("Invoice queued for reprocessing.");
      router.push("/exceptions");
    } catch {
      toast.error("Failed to retry.");
    }
  }

  async function handleResolve() {
    const notes = await prompt({
      title: "Resolve exception",
      description: "Add optional resolution notes for the record.",
      placeholder: "Resolution notes (optional)",
      confirmLabel: "Resolve",
      multiline: true,
    });
    if (notes === null) return;
    try {
      await resolveException.mutateAsync({ intakeId, notes: notes || undefined });
      toast.success("Exception resolved.");
      router.push("/exceptions");
    } catch {
      toast.error("Failed to resolve exception.");
    }
  }

  async function loadPartialExtraction() {
    if (partialData) {
      setShowPartial(!showPartial);
      return;
    }
    setLoadingPartial(true);
    try {
      const resp = await fetch(`/api/v1/exceptions/${intakeId}/partial`);
      if (resp.ok) {
        const data = await resp.json();
        setPartialData(data);
        setShowPartial(true);
      } else {
        toast.error("Partial extraction not available.");
      }
    } catch {
      toast.error("Failed to load partial extraction.");
    } finally {
      setLoadingPartial(false);
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-6 max-w-4xl">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-48" />
        <Skeleton className="h-48" />
      </div>
    );
  }

  if (isError || !exception) {
    return (
      <div className="space-y-6 max-w-4xl">
        <Link href="/exceptions">
          <Button variant="ghost" size="sm">
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Exceptions
          </Button>
        </Link>
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-sm text-destructive">
              Exception not found or failed to load.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-4xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-4">
          <Link href="/exceptions">
            <Button variant="ghost" size="sm">
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back
            </Button>
          </Link>
          <div>
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-warning" />
              <h1 className="text-xl font-bold tracking-tight">
                {exception.original_filename ?? "Exception Detail"}
              </h1>
            </div>
            <p className="text-sm text-muted-foreground mt-0.5 font-mono">
              {intakeId}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {exception.has_original_pdf && (
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                window.open(`/api/v1/exceptions/${intakeId}/pdf`, "_blank")
              }
            >
              <FileDown className="h-4 w-4" />
              Download PDF
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={handleRetry}
            disabled={retryException.isPending}
          >
            <RefreshCw className="h-4 w-4" />
            Retry
          </Button>
          <Button
            size="sm"
            onClick={handleResolve}
            disabled={resolveException.isPending}
          >
            <CheckCircle2 className="h-4 w-4" />
            Resolve
          </Button>
        </div>
      </div>

      {/* Error Summary */}
      <Card className="border-warning/30 bg-warning/12">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-warning" />
            Error Information
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-center gap-3">
            <span className="text-xs font-medium text-muted-foreground w-20">Reason</span>
            <Badge
              variant="outline"
              className={reasonColors[exception.reason] ?? reasonColors.extraction_error}
            >
              {formatReason(exception.reason)}
            </Badge>
          </div>
          <div className="flex items-start gap-3">
            <span className="text-xs font-medium text-muted-foreground w-20 pt-0.5">Message</span>
            <p className="text-sm">{exception.message || "No message provided."}</p>
          </div>
          {exception.timestamp && (
            <div className="flex items-center gap-3">
              <span className="text-xs font-medium text-muted-foreground w-20">Time</span>
              <span className="text-sm text-muted-foreground">
                {format(new Date(exception.timestamp), "MMM d, yyyy HH:mm:ss")}
              </span>
            </div>
          )}
          <div className="flex items-center gap-3">
            <span className="text-xs font-medium text-muted-foreground w-20">Status</span>
            <Badge variant="secondary">{exception.status}</Badge>
          </div>
        </CardContent>
      </Card>

      {/* Error Details (raw JSON) */}
      {exception.details && Object.keys(exception.details).length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Info className="h-4 w-4" />
              Error Details
            </CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="text-xs font-mono whitespace-pre-wrap overflow-auto max-h-64 bg-muted p-4 rounded-lg">
              {JSON.stringify(exception.details, null, 2)}
            </pre>
          </CardContent>
        </Card>
      )}

      {/* Processing Timeline */}
      {invoice?.logs && invoice.logs.length > 0 && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Clock className="h-4 w-4" />
              Processing Timeline
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-0">
              {invoice.logs.map((log, idx) => (
                <div key={log.id} className="flex gap-3">
                  {/* Timeline connector */}
                  <div className="flex flex-col items-center">
                    <div
                      className={`w-2.5 h-2.5 rounded-sm mt-1.5 ${
                        log.status === "completed"
                          ? "bg-success/12"
                          : log.status === "failed"
                            ? "bg-destructive/12"
                            : "bg-warning/12"
                      }`}
                    />
                    {idx < invoice.logs.length - 1 && (
                      <div className="w-px flex-1 bg-border" />
                    )}
                  </div>

                  {/* Content */}
                  <div className="pb-4 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{log.step}</span>
                      <span
                        className={`text-xs ${stepStatusColors[log.status] ?? "text-muted-foreground"}`}
                      >
                        {log.status}
                      </span>
                    </div>
                    {log.message && (
                      <p className="text-xs text-muted-foreground mt-0.5 break-words">
                        {log.message}
                      </p>
                    )}
                    <span className="text-xs text-muted-foreground/60">
                      {format(new Date(log.timestamp), "HH:mm:ss.SSS")}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Partial Extraction */}
      {exception.has_partial_extraction && (
        <Card>
          <CardHeader className="pb-3">
            <button
              className="flex items-center gap-2 w-full text-left"
              onClick={loadPartialExtraction}
              disabled={loadingPartial}
            >
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                <FileText className="h-4 w-4" />
                Partial Extraction Data
                {showPartial ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronRight className="h-4 w-4" />
                )}
              </CardTitle>
            </button>
          </CardHeader>
          {showPartial && partialData && (
            <CardContent>
              <pre className="text-xs font-mono whitespace-pre-wrap overflow-auto max-h-96 bg-muted p-4 rounded-lg">
                {JSON.stringify(partialData, null, 2)}
              </pre>
            </CardContent>
          )}
        </Card>
      )}

      {/* Quick Actions */}
      {exception.reason === "unknown_supplier" && (
        <Card>
          <CardContent className="py-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <Info className="h-5 w-5 text-primary" />
                <div>
                  <p className="text-sm font-medium">Unknown Supplier</p>
                  <p className="text-xs text-muted-foreground">
                    Create a supplier profile to handle this invoice type in the future.
                  </p>
                </div>
              </div>
              <Link href="/profiles/new">
                <Button variant="outline" size="sm">
                  <Plus className="h-4 w-4" />
                  Create Profile
                </Button>
              </Link>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
