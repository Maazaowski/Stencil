"use client";

import { use } from "react";
import Link from "next/link";
import {
  useInvoice,
  useInvoicePreview,
  useRejectInvoice,
  useCancelInvoice,
} from "@/hooks/use-invoices";
import { ExtractionPreviewView } from "@/components/extraction-preview";
import { ReconciliationPanel } from "@/components/reconciliation-panel";
import { safeFormatDate } from "@/lib/format-date";
import { resolveXlsxOutputFilename } from "@/lib/output-naming";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  StatusBadge,
  ExtractionPathBadge,
  ReconciliationBadge,
} from "@/components/status-badge";
import { ProcessingTimeline } from "@/components/processing-timeline";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  ArrowLeft,
  FileText,
  Download,
  DollarSign,
  Cpu,
  AlertTriangle,
  CheckCircle2,
  Ban,
} from "lucide-react";
import type { ExtractionJob } from "@/types";

export default function InvoiceDetailPage({
  params,
}: {
  params: Promise<{ intakeId: string }>;
}) {
  const { intakeId } = use(params);
  const { data: invoice, isLoading, isError } = useInvoice(intakeId);
  const rejectInvoice = useRejectInvoice();
  const cancelInvoice = useCancelInvoice();
  const confirm = useConfirm();
  const { data: preview } = useInvoicePreview(
    intakeId,
    invoice?.status === "completed" ||
      invoice?.status === "completed_with_warnings",
  );

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-64" />
        <div className="grid gap-4 md:grid-cols-2">
          <Skeleton className="h-48" />
          <Skeleton className="h-48" />
        </div>
      </div>
    );
  }

  if (isError || !invoice) {
    return (
      <div className="space-y-6">
        <Link href="/invoices">
          <Button variant="ghost" size="sm">
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Invoices
          </Button>
        </Link>
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-sm text-destructive">
              Invoice not found or failed to load.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const job: ExtractionJob | undefined = invoice.jobs?.[0];
  const xlsxFilename = resolveXlsxOutputFilename(
    job?.xlsx_output_filename,
    invoice.original_filename,
  );

  const isModelExtracted = job?.extraction_path === "model" || job?.extraction_path === "model_fallback_ai";
  const isDelivered = invoice.status === "completed" || invoice.status === "completed_with_warnings";
  const canReject = isDelivered && isModelExtracted;
  const isProcessing = invoice.status === "processing" || invoice.status === "received";
  const trainingIncomplete =
    invoice.intake_source === "training" &&
    invoice.status === "completed_with_warnings" &&
    invoice.logs?.some(
      (log) =>
        (log.step === "model_generation" && log.status === "failed") ||
        (log.step === "model_training" && log.status === "failed") ||
        (log.step === "model_validation" && log.status === "failed"),
    );

  async function handleCancel() {
    const ok = await confirm({
      title: "Cancel processing for this invoice?",
      confirmLabel: "Cancel processing",
      variant: "destructive",
    });
    if (!ok) return;
    cancelInvoice.mutate(intakeId, {
      onSuccess: () => toast.success("Processing cancelled."),
      onError: () => toast.error("Failed to cancel processing."),
    });
  }

  async function handleReject() {
    const ok = await confirm({
      title: "Reject this extraction and regenerate via AI?",
      description:
        "This will retire the extraction model that produced this output and re-process the invoice using AI.",
      confirmLabel: "Reject & re-process",
      variant: "destructive",
    });
    if (!ok) return;
    rejectInvoice.mutate(intakeId, {
      onSuccess: () => toast.success("Invoice rejected. Re-processing via AI..."),
      onError: () => toast.error("Failed to reject invoice"),
    });
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link href="/invoices">
            <Button variant="ghost" size="sm">
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back
            </Button>
          </Link>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold tracking-tight">
                {invoice.original_filename}
              </h1>
              <StatusBadge status={invoice.status} />
            </div>
            <p className="text-sm text-muted-foreground font-mono">
              {invoice.id}
              {invoice.intake_source && (
                <Badge variant="outline" className="ml-2 text-xs capitalize">
                  {invoice.intake_source}
                </Badge>
              )}
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          {isProcessing && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleCancel}
              disabled={cancelInvoice.isPending}
            >
              <Ban className="h-4 w-4 mr-2" />
              {cancelInvoice.isPending ? "Cancelling..." : "Cancel"}
            </Button>
          )}
        {isDelivered && (
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              render={
                <a
                  href={`/api/v1/invoices/${invoice.id}/output/${encodeURIComponent(xlsxFilename)}`}
                  download={xlsxFilename}
                />
              }
            >
              <Download className="h-4 w-4 mr-2" />
              XLSX
            </Button>
            <Button
              variant="outline"
              size="sm"
              render={
                <a
                  href={`/api/v1/invoices/${invoice.id}/output/canonical_invoice.json`}
                  download="canonical_invoice.json"
                />
              }
            >
              <Download className="h-4 w-4 mr-2" />
              JSON
            </Button>
            {canReject && (
              <Button
                variant="destructive"
                size="sm"
                onClick={handleReject}
                disabled={rejectInvoice.isPending}
              >
                <AlertTriangle className="h-4 w-4 mr-2" />
                {rejectInvoice.isPending ? "Rejecting..." : "Reject & Regenerate"}
              </Button>
            )}
          </div>
        )}
        </div>
      </div>

      {/* Warning banner — delivered, but something needs attention */}
      {invoice.status === "completed_with_warnings" && (
        <div className="flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/12 px-4 py-3 text-sm text-warning">
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
          <div>
            <p className="font-medium">Delivered with warnings</p>
            <p className="text-warning">
              {invoice.error_message ||
                "The invoice was extracted and delivered, but a check needs review."}
            </p>
          </div>
        </div>
      )}

      {/*
        Reconciliation FIRST. The operator's task here is "find the row that is
        wrong", so the arithmetic leads and everything else supports it.
      */}
      {isDelivered && preview?.reconciliation && (
        <ReconciliationPanel
          reconciliation={preview.reconciliation}
          lineItemCount={preview.line_item_count}
        />
      )}

      {/* The delivered rows — where the wrong one actually lives. */}
      {isDelivered && preview && (
        <section className="flex flex-col gap-2">
          <h2 className="label-mono">Delivered rows</h2>
          <ExtractionPreviewView preview={preview} hideReconciliation />
        </section>
      )}

      {/*
        The processing log is diagnostic, not the task. Collapsed by default —
        it used to occupy the top of the page above the actual output. Stays
        open while the invoice is still running, when it IS the task.
      */}
      <details className="border border-border bg-card" open={isProcessing}>
        <summary className="cursor-pointer px-3 py-2 text-[0.8125rem] font-medium select-none hover:bg-accent">
          Processing log
        </summary>
        <div className="border-t border-border p-3">
          <ProcessingTimeline intakeId={intakeId} showCancel={false} />
        </div>
      </details>

      {/* Info Cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <FileText className="h-4 w-4" />
              Document
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Pages</span>
              <span>{invoice.page_count}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Size</span>
              <span>{(invoice.file_size_bytes / 1024).toFixed(0)} KB</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Received</span>
              <span>{safeFormatDate(invoice.created_at, "MMM d, HH:mm")}</span>
            </div>
            {job?.started_at && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Started</span>
                <span>{safeFormatDate(job.started_at, "MMM d, HH:mm")}</span>
              </div>
            )}
            {job?.completed_at && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">Completed</span>
                <span>{safeFormatDate(job.completed_at, "MMM d, HH:mm")}</span>
              </div>
            )}
          </CardContent>
        </Card>

        {job && (
          <>
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                  <Cpu className="h-4 w-4" />
                  Extraction
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1 text-sm">
                <div className="flex justify-between items-center">
                  <span className="text-muted-foreground">Path</span>
                  <ExtractionPathBadge path={job.extraction_path} />
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-muted-foreground">Reconciled</span>
                  <ReconciliationBadge
                    isReconciled={job.is_reconciled}
                    variance={job.reconciliation_variance}
                  />
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Line Items</span>
                  <span>{job.line_item_count}</span>
                </div>
                {job.supplier_name && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Supplier</span>
                    <span>{job.supplier_name}</span>
                  </div>
                )}
                {trainingIncomplete && (
                  <p className="text-xs text-warning pt-1 border-t mt-2">
                    AI output delivered; model training did not complete. See
                    processing log for comparison files.
                  </p>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                  <DollarSign className="h-4 w-4" />
                  AI Cost
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Cost</span>
                  <span>${job.estimated_cost_usd.toFixed(4)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Input Tokens</span>
                  <span>{job.tokens_input.toLocaleString()}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Output Tokens</span>
                  <span>{job.tokens_output.toLocaleString()}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Duration</span>
                  <span>{(job.extraction_duration_ms / 1000).toFixed(1)}s</span>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground flex items-center gap-2">
                  {job.is_reconciled ? (
                    <CheckCircle2 className="h-4 w-4 text-success" />
                  ) : (
                    <AlertTriangle className="h-4 w-4 text-warning" />
                  )}
                  Reconciliation
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-1 text-sm">
                <div className="flex justify-between items-center">
                  <span className="text-muted-foreground">Status</span>
                  <Badge
                    variant="outline"
                    className={
                      job.is_reconciled
                        ? "bg-success/12 text-success border-success/30"
                        : "bg-warning/12 text-warning border-warning/30"
                    }
                  >
                    {job.is_reconciled ? "Reconciled" : "Variance"}
                  </Badge>
                </div>
                {job.reconciliation_variance !== null && (
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Variance</span>
                    <span>
                      {(job.reconciliation_variance * 100).toFixed(1)}%
                    </span>
                  </div>
                )}
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Model</span>
                  <span className="text-xs font-mono">
                    {job.ai_model_name ?? "—"}
                  </span>
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </div>

      {/* Tabs: Errors only — timeline is shown above */}
      {job?.error_message && (
        <Tabs defaultValue="errors">
          <TabsList>
            <TabsTrigger value="errors">Errors</TabsTrigger>
          </TabsList>
          <TabsContent value="errors" className="mt-4">
            <Card className="border-destructive">
              <CardContent className="pt-6">
                <pre className="text-xs text-destructive whitespace-pre-wrap font-mono">
                  {job.error_message}
                </pre>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
