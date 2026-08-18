"use client";

import { useRouter } from "next/navigation";
import {
  useExceptions,
  useRetryException,
  useResolveException,
} from "@/hooks/use-exceptions";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { format } from "date-fns";
import {
  AlertTriangle,
  RefreshCw,
  CheckCircle2,
  ExternalLink,
  Inbox,
} from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { usePrompt } from "@/components/ui/confirm-dialog";
import type { ExceptionRecord } from "@/types";

const reasonColors: Record<string, string> = {
  low_confidence: "bg-warning/12 text-warning border-warning/30",
  reconciliation_failure: "bg-destructive/12 text-destructive border-destructive/30",
  schema_validation: "bg-destructive/12 text-destructive border-destructive/30",
  extraction_error: "bg-destructive/12 text-destructive border-destructive/30",
  unknown_supplier: "bg-muted text-muted-foreground border-border-foreground",
  unreadable_pdf: "bg-muted text-muted-foreground border-border-foreground",
};

function formatReason(reason: string): string {
  return reason
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function ExceptionsPage() {
  const router = useRouter();
  const { data: exceptions, isLoading, isError } = useExceptions();
  const retryException = useRetryException();
  const resolveException = useResolveException();
  const prompt = usePrompt();

  async function handleRetry(intakeId: string) {
    try {
      await retryException.mutateAsync(intakeId);
      toast.success("Invoice queued for reprocessing.");
    } catch {
      toast.error("Failed to retry.");
    }
  }

  async function handleResolve(intakeId: string) {
    const notes = await prompt({
      title: "Resolve exception",
      description: "Add optional resolution notes for the record.",
      placeholder: "Resolution notes (optional)",
      confirmLabel: "Resolve",
      multiline: true,
    });
    if (notes === null) return; // cancelled
    try {
      await resolveException.mutateAsync({ intakeId, notes: notes || undefined });
      toast.success("Exception resolved.");
    } catch {
      toast.error("Failed to resolve exception.");
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Exception Queue"
        description="Invoices that failed or need manual review. Retry or resolve each item."
      />

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      ) : isError ? (
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-sm text-destructive">
              Failed to load exceptions. Is the backend running?
            </p>
          </CardContent>
        </Card>
      ) : !exceptions || exceptions.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <Inbox className="h-10 w-10 text-muted-foreground mb-3" />
            <p className="text-sm text-muted-foreground">
              No exceptions. All invoices processed successfully.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {exceptions.map((exc: ExceptionRecord) => (
            <Card key={exc.intake_id}>
              <CardContent className="py-4">
                <div className="flex items-start gap-4">
                  {/* Icon */}
                  <AlertTriangle className="h-5 w-5 text-warning shrink-0 mt-0.5" />

                  {/* Content */}
                  <div className="flex-1 min-w-0 space-y-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-sm">
                        {exc.original_filename ?? exc.intake_id.slice(0, 12)}
                      </span>
                      <Badge
                        variant="outline"
                        className={
                          reasonColors[exc.reason] ??
                          reasonColors.extraction_error
                        }
                      >
                        {formatReason(exc.reason)}
                      </Badge>
                      {exc.status && (
                        <Badge variant="secondary" className="text-xs">
                          {exc.status}
                        </Badge>
                      )}
                    </div>

                    {exc.message && (
                      <p className="text-sm text-muted-foreground">
                        {exc.message}
                      </p>
                    )}

                    <div className="flex items-center gap-4 text-xs text-muted-foreground">
                      <span className="font-mono">{exc.intake_id.slice(0, 16)}...</span>
                      {exc.timestamp && (
                        <span>
                          {format(
                            new Date(exc.timestamp),
                            "MMM d, yyyy HH:mm"
                          )}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex flex-wrap gap-2 shrink-0">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        router.push(`/invoices/${exc.intake_id}`)
                      }
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                      View
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleRetry(exc.intake_id)}
                      disabled={retryException.isPending}
                    >
                      <RefreshCw className="h-3.5 w-3.5" />
                      Retry
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => handleResolve(exc.intake_id)}
                      disabled={resolveException.isPending}
                    >
                      <CheckCircle2 className="h-3.5 w-3.5" />
                      Resolve
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
