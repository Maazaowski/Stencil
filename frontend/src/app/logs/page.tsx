"use client";

import { useState } from "react";
import { usePipelineLogs, usePipelineJobs, useCostTrends } from "@/hooks/use-pipeline";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Search,
  ChevronLeft,
  ChevronRight,
  Activity,
  DollarSign,
  Zap,
  Clock,
} from "lucide-react";
import { format } from "date-fns";
import Link from "next/link";
import { PageHeader } from "@/components/page-header";

// ── Status color helpers ────────────────────────────────────

const stepColors: Record<string, string> = {
  intake: "bg-primary/12 text-primary border-primary/30",
  fingerprint: "bg-primary/12 text-primary border-primary/30",
  routing: "bg-primary/12 text-primary border-primary/30",
  classification: "bg-primary/12 text-primary border-primary/30",
  ai_extraction: "bg-warning/12 text-warning border-warning/30",
  reconciliation: "bg-warning/12 text-warning border-warning/30",
  output: "bg-success/12 text-success border-success/30",
  output_copy: "bg-success/12 text-success border-success/30",
  pipeline: "bg-destructive/12 text-destructive border-destructive/30",
};

const statusColors: Record<string, string> = {
  completed: "bg-success/12 text-success border-success/30",
  failed: "bg-destructive/12 text-destructive border-destructive/30",
  low_confidence: "bg-warning/12 text-warning border-warning/30",
  fallback_to_ai: "bg-warning/12 text-warning border-warning/30",
  running: "bg-primary/12 text-primary border-primary/30",
};

const pathColors: Record<string, string> = {
  ai: "bg-warning/12 text-warning border-warning/30",
};

// ── Tabs ────────────────────────────────────────────────────

type Tab = "logs" | "tokens";

export default function LogsPage() {
  const [tab, setTab] = useState<Tab>("logs");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Audit Log & Usage"
        description="Processing audit trail and AI token usage reporting."
      />

      {/* Tab switcher */}
      <div className="flex gap-1 border-b">
        <button
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            tab === "logs"
              ? "border-primary text-primary"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
          onClick={() => setTab("logs")}
        >
          <Activity className="inline h-4 w-4 mr-1.5 -mt-0.5" />
          Processing Logs
        </button>
        <button
          className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
            tab === "tokens"
              ? "border-primary text-primary"
              : "border-transparent text-muted-foreground hover:text-foreground"
          }`}
          onClick={() => setTab("tokens")}
        >
          <DollarSign className="inline h-4 w-4 mr-1.5 -mt-0.5" />
          Token Usage & Cost
        </button>
      </div>

      {tab === "logs" ? <ProcessingLogsTab /> : <TokenUsageTab />}
    </div>
  );
}

// ── Processing Logs Tab ─────────────────────────────────────

function ProcessingLogsTab() {
  const [page, setPage] = useState(1);
  const [intakeFilter, setIntakeFilter] = useState("");
  const [stepFilter, setStepFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  const { data, isLoading, isError } = usePipelineLogs({
    page,
    per_page: 25,
    intake_id: intakeFilter || undefined,
    step: stepFilter || undefined,
    status: statusFilter || undefined,
  });

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap gap-3">
        <div className="relative w-64">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Filter by intake ID..."
            className="pl-9"
            value={intakeFilter}
            onChange={(e) => {
              setIntakeFilter(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <Select
          value={stepFilter}
          onValueChange={(v) => {
            setStepFilter(v === "all" ? "" : (v ?? ""));
            setPage(1);
          }}
        >
          <SelectTrigger className="w-44">
            <SelectValue placeholder="All steps" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All steps</SelectItem>
            <SelectItem value="intake">Intake</SelectItem>
            <SelectItem value="fingerprint">Fingerprint</SelectItem>
            <SelectItem value="routing">Routing</SelectItem>
            <SelectItem value="classification">Classification</SelectItem>
            <SelectItem value="ai_extraction">AI Extraction</SelectItem>
            <SelectItem value="reconciliation">Reconciliation</SelectItem>
            <SelectItem value="output">Output</SelectItem>
            <SelectItem value="pipeline">Pipeline</SelectItem>
          </SelectContent>
        </Select>
        <Select
          value={statusFilter}
          onValueChange={(v) => {
            setStatusFilter(v === "all" ? "" : (v ?? ""));
            setPage(1);
          }}
        >
          <SelectTrigger className="w-40">
            <SelectValue placeholder="All statuses" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="completed">Completed</SelectItem>
            <SelectItem value="failed">Failed</SelectItem>
            <SelectItem value="low_confidence">Low Confidence</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : isError ? (
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-sm text-destructive">
              Failed to load processing logs. Is the backend running?
            </p>
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="rounded-md border overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="text-left px-4 py-2.5 font-medium">Timestamp</th>
                  <th className="text-left px-4 py-2.5 font-medium">Intake ID</th>
                  <th className="text-left px-4 py-2.5 font-medium">Step</th>
                  <th className="text-left px-4 py-2.5 font-medium">Status</th>
                  <th className="text-left px-4 py-2.5 font-medium">Message</th>
                </tr>
              </thead>
              <tbody>
                {data && data.items.length > 0 ? (
                  data.items.map((log) => (
                    <tr key={log.id} className="border-b hover:bg-muted/30 transition-colors">
                      <td className="px-4 py-2.5 text-xs text-muted-foreground whitespace-nowrap">
                        {format(new Date(log.timestamp), "MMM d, HH:mm:ss")}
                      </td>
                      <td className="px-4 py-2.5">
                        <Link
                          href={`/invoices/${log.intake_id}`}
                          className="font-mono text-xs text-primary hover:underline"
                        >
                          {log.intake_id.slice(0, 12)}...
                        </Link>
                      </td>
                      <td className="px-4 py-2.5">
                        <Badge
                          variant="outline"
                          className={`text-xs ${stepColors[log.step] ?? ""}`}
                        >
                          {log.step}
                        </Badge>
                      </td>
                      <td className="px-4 py-2.5">
                        <Badge
                          variant="outline"
                          className={`text-xs ${statusColors[log.status] ?? ""}`}
                        >
                          {log.status}
                        </Badge>
                      </td>
                      <td className="px-4 py-2.5 text-xs text-muted-foreground max-w-md truncate">
                        {log.message ?? "—"}
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">
                      No processing logs found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {data && data.pages > 1 && (
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                Page {data.page} of {data.pages} ({data.total} total)
              </p>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => p - 1)}
                >
                  <ChevronLeft className="h-4 w-4" />
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= data.pages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Token Usage & Cost Tab ──────────────────────────────────

function TokenUsageTab() {
  const [page, setPage] = useState(1);
  const { data: jobs, isLoading } = usePipelineJobs({
    page,
    per_page: 20,
    extraction_path: "ai",
  });

  const { data: costs } = useCostTrends(30);

  // Summary stats from current jobs page
  const aiJobs = jobs?.items.filter((j) => j.extraction_path === "ai").length ?? 0;

  // All-time cost from trends
  const allTimeCost = costs?.reduce((sum, c) => sum + c.cost, 0) ?? 0;

  return (
    <div className="space-y-6">
      {/* Summary cards */}
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Total AI Cost (30d)</CardTitle>
            <DollarSign className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">${allTimeCost.toFixed(2)}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">AI Extractions</CardTitle>
            <Zap className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{aiJobs}</div>
            <p className="text-xs text-muted-foreground">on this page</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm font-medium">Avg Duration</CardTitle>
            <Clock className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {jobs?.items.length
                ? (
                    jobs.items.reduce((s, j) => s + (j.extraction_duration_ms ?? 0), 0) /
                    jobs.items.length /
                    1000
                  ).toFixed(1) + "s"
                : "—"}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Cost trend (simple text-based since we don't have a chart library) */}
      {costs && costs.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Daily AI Cost (Last 30 Days)</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-end gap-1 h-32">
              {costs.map((point) => {
                const maxCost = Math.max(...costs.map((c) => c.cost), 0.01);
                const height = Math.max((point.cost / maxCost) * 100, 2);
                return (
                  <div
                    key={point.date}
                    className="flex-1 bg-primary/20 hover:bg-primary/40 rounded-t transition-colors relative group"
                    style={{ height: `${height}%` }}
                  >
                    <div className="absolute -top-8 left-1/2 -translate-x-1/2 hidden group-hover:block bg-popover border rounded px-2 py-1 text-xs whitespace-nowrap shadow-md z-10">
                      {point.date}: ${point.cost.toFixed(3)}
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="flex justify-between mt-1 text-xs text-muted-foreground">
              <span>{costs[0]?.date}</span>
              <span>{costs[costs.length - 1]?.date}</span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Jobs table */}
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : (
        <>
          <div className="rounded-md border overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b bg-muted/50">
                  <th className="text-left px-4 py-2.5 font-medium">Date</th>
                  <th className="text-left px-4 py-2.5 font-medium">Intake</th>
                  <th className="text-left px-4 py-2.5 font-medium">Supplier</th>
                  <th className="text-left px-4 py-2.5 font-medium">Path</th>
                  <th className="text-left px-4 py-2.5 font-medium">AI Model</th>
                  <th className="text-right px-4 py-2.5 font-medium">Tokens In</th>
                  <th className="text-right px-4 py-2.5 font-medium">Tokens Out</th>
                  <th className="text-right px-4 py-2.5 font-medium">Cost</th>
                  <th className="text-right px-4 py-2.5 font-medium">Duration</th>
                  <th className="text-right px-4 py-2.5 font-medium">Confidence</th>
                </tr>
              </thead>
              <tbody>
                {jobs && jobs.items.length > 0 ? (
                  jobs.items.map((job) => (
                    <tr key={job.id} className="border-b hover:bg-muted/30 transition-colors">
                      <td className="px-4 py-2.5 text-xs text-muted-foreground whitespace-nowrap">
                        {format(new Date(job.created_at), "MMM d, HH:mm")}
                      </td>
                      <td className="px-4 py-2.5">
                        <Link
                          href={`/invoices/${job.intake_id}`}
                          className="font-mono text-xs text-primary hover:underline"
                        >
                          {job.intake_id.slice(0, 12)}...
                        </Link>
                      </td>
                      <td className="px-4 py-2.5 text-xs">
                        {job.supplier_name ?? "—"}
                      </td>
                      <td className="px-4 py-2.5">
                        <Badge
                          variant="outline"
                          className={`text-xs ${pathColors[job.extraction_path] ?? ""}`}
                        >
                          {job.extraction_path}
                        </Badge>
                      </td>
                      <td className="px-4 py-2.5 text-xs text-muted-foreground">
                        {job.ai_model_name ?? "—"}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-right font-mono">
                        {job.tokens_input > 0 ? job.tokens_input.toLocaleString() : "—"}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-right font-mono">
                        {job.tokens_output > 0 ? job.tokens_output.toLocaleString() : "—"}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-right font-mono">
                        {job.estimated_cost_usd > 0
                          ? `$${job.estimated_cost_usd.toFixed(4)}`
                          : "—"}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-right font-mono">
                        {job.extraction_duration_ms > 0
                          ? `${(job.extraction_duration_ms / 1000).toFixed(1)}s`
                          : "—"}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-right">
                        {job.overall_confidence > 0 ? (
                          <span
                            className={
                              job.overall_confidence >= 0.9
                                ? "text-success"
                                : job.overall_confidence >= 0.75
                                  ? "text-warning"
                                  : "text-destructive"
                            }
                          >
                            {(job.overall_confidence * 100).toFixed(0)}%
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={10} className="px-4 py-8 text-center text-muted-foreground">
                      No extraction jobs found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {jobs && jobs.pages > 1 && (
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                Page {jobs.page} of {jobs.pages} ({jobs.total} total)
              </p>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => p - 1)}
                >
                  <ChevronLeft className="h-4 w-4" />
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= jobs.pages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
