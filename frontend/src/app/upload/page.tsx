"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { useDropzone } from "react-dropzone";
import { safeFormatDate } from "@/lib/format-date";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  FileText,
  Loader2,
  Upload,
  X,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useUploadInvoice, useInvoices } from "@/hooks/use-invoices";
import { PipelineProgress } from "@/components/pipeline-progress";
import { StatusBadge } from "@/components/status-badge";
import { PageHeader } from "@/components/page-header";
import { cn } from "@/lib/utils";

interface QueueItem {
  id: string;
  file: File;
  status: "pending" | "uploading" | "success" | "error";
  message?: string;
  intakeId?: string;
}

export default function UploadPage() {
  const router = useRouter();
  const uploadInvoice = useUploadInvoice();
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [isUploading, setIsUploading] = useState(false);

  const { data: recentData } = useInvoices({ page: 1, per_page: 10 });

  const processingIntakeIds = queue
    .filter((item) => item.status === "success" && item.intakeId)
    .map((item) => item.intakeId as string);

  const onDrop = useCallback(
    (acceptedFiles: File[], rejectedFiles: { file: File }[]) => {
      if (rejectedFiles.length > 0) {
        toast.error("Only PDF files are accepted.");
      }
      const newItems: QueueItem[] = acceptedFiles.map((file) => ({
        id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
        file,
        status: "pending",
      }));
      setQueue((prev) => [...prev, ...newItems]);
    },
    []
  );

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    accept: { "application/pdf": [".pdf"] },
    multiple: true,
    noClick: true,
    maxSize: 50 * 1024 * 1024,
    onDrop,
  });

  function removeFromQueue(id: string) {
    setQueue((prev) => prev.filter((item) => item.id !== id));
  }

  function clearCompleted() {
    setQueue((prev) =>
      prev.filter((item) => item.status !== "success" && item.status !== "error")
    );
  }

  async function uploadAll() {
    const pending = queue.filter((item) => item.status === "pending");
    if (pending.length === 0) return;

    setIsUploading(true);
    let uploadedCount = 0;

    for (const item of pending) {
      setQueue((prev) =>
        prev.map((q) =>
          q.id === item.id ? { ...q, status: "uploading" } : q
        )
      );

      try {
        const result = await uploadInvoice.mutateAsync(item.file);
        setQueue((prev) =>
          prev.map((q) =>
            q.id === item.id
              ? {
                  ...q,
                  status: "success",
                  message: `Queued - ${result.status}`,
                  intakeId: result.id,
                }
              : q
          )
        );
        uploadedCount += 1;
      } catch {
        setQueue((prev) =>
          prev.map((q) =>
            q.id === item.id
              ? {
                  ...q,
                  status: "error",
                  message: "Upload failed",
                }
              : q
          )
        );
      }
    }

    setIsUploading(false);

    if (uploadedCount > 0) {
      toast.success(
        `${uploadedCount} invoice${uploadedCount > 1 ? "s" : ""} queued for processing.`
      );
    }
  }

  const pendingCount = queue.filter((q) => q.status === "pending").length;
  const hasCompleted = queue.some(
    (q) => q.status === "success" || q.status === "error"
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Upload Invoices"
        description="Drop PDF invoices to start extraction. Multiple files supported."
      />

      <Card>
        <CardContent className="pt-6">
          <div
            {...getRootProps()}
            className={cn(
              "flex min-h-[200px] cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 text-center transition-colors",
              isDragActive
                ? "border-primary bg-primary/5"
                : "border-muted-foreground/25 hover:border-muted-foreground/50"
            )}
            onClick={open}
          >
            <input {...getInputProps()} />
            <Upload className="mb-3 h-10 w-10 text-muted-foreground" />
            {isDragActive ? (
              <p className="text-sm font-medium text-primary">
                Drop PDF files here...
              </p>
            ) : (
              <>
                <p className="text-sm font-medium">
                  Drag and drop PDFs here, or click to browse
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  PDF files only, up to 50 MB each
                </p>
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {queue.length > 0 && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-3">
            <CardTitle className="text-base">
              Upload Queue ({queue.length} file{queue.length !== 1 ? "s" : ""})
            </CardTitle>
            <div className="flex gap-2">
              {hasCompleted && (
                <Button variant="ghost" size="sm" onClick={clearCompleted}>
                  Clear completed
                </Button>
              )}
              {pendingCount > 0 && (
                <Button size="sm" onClick={uploadAll} disabled={isUploading}>
                  {isUploading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Upload className="h-4 w-4" />
                  )}
                  Upload {pendingCount} file{pendingCount !== 1 ? "s" : ""}
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {queue.map((item) => (
                <div
                  key={item.id}
                  className="flex items-center gap-3 rounded-lg border bg-muted/30 p-3"
                >
                  {item.status === "uploading" && (
                    <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
                  )}
                  {item.status === "success" && (
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-success" />
                  )}
                  {item.status === "error" && (
                    <AlertTriangle className="h-4 w-4 shrink-0 text-destructive" />
                  )}
                  {item.status === "pending" && (
                    <Clock className="h-4 w-4 shrink-0 text-muted-foreground" />
                  )}

                  <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {item.file.name}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {(item.file.size / 1024 / 1024).toFixed(2)} MB
                      {item.message && <span className="ml-2">- {item.message}</span>}
                    </p>
                  </div>

                  {item.status === "pending" && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      onClick={() => removeFromQueue(item.id)}
                    >
                      <X className="h-3 w-3" />
                    </Button>
                  )}
                  {item.status === "success" && item.intakeId && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => router.push(`/invoices/${item.intakeId}`)}
                    >
                      View
                    </Button>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {processingIntakeIds.length > 0 && (
        <div className="space-y-4">
          <h3 className="text-sm font-medium text-muted-foreground">
            Processing progress
          </h3>
          {processingIntakeIds.map((id) => (
            <PipelineProgress key={id} intakeId={id} />
          ))}
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent Invoices</CardTitle>
        </CardHeader>
        <CardContent>
          {recentData?.items && recentData.items.length > 0 ? (
            <div className="space-y-2">
              {recentData.items.map((invoice) => (
                <div
                  key={invoice.id}
                  className="flex cursor-pointer items-center gap-3 rounded-lg border p-3 transition-colors hover:bg-muted/50"
                  onClick={() => router.push(`/invoices/${invoice.id}`)}
                >
                  <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {invoice.original_filename}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {safeFormatDate(invoice.created_at, "MMM d, yyyy HH:mm")}
                      {" - "}
                      {invoice.page_count} page
                      {invoice.page_count !== 1 ? "s" : ""}
                    </p>
                  </div>
                  <StatusBadge status={invoice.status} />
                </div>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              No invoices yet. Upload a PDF to get started.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
