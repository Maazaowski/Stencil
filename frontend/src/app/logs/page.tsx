"use client";

import { useState } from "react";
import { usePipelineLogs, usePipelineJobs, useCostTrends } from "@/hooks/use-pipeline";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ExtractionPathBadge } from "@/components/status-badge";
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
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Timestamp</TableHead>
                <TableHead>Intake</TableHead>
                <TableHead>Step</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Message</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data && data.items.length > 0 ? (
                data.items.map((log) => (
                  <TableRow key={log.id}>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {format(new Date(log.timestamp), "dd MMM HH:mm:ss")}
                    </TableCell>
                    <TableCell>
                      <Link
                        href={`/invoices/${log.intake_id}`}
                        className="font-mono text-xs text-primary hover:underline"
                      >
                        {log.intake_id.slice(0, 8)}
                      </Link>
                    </TableCell>
                    <TableCell>
                      <Badge variant="neutral">{log.step}</Badge>
                    </TableCell>
                    <TableCell>
                      {/* Silence for the ordinary case — a log full of green
                          "completed" tags is a log you stop reading. */}
                      {log.status === "completed" ? (
                        <span className="text-muted-foreground/70">—</span>
                      ) : (
                        <Badge
                          variant={
                            log.status === "failed"
                              ? "destructive"
                              : log.status === "skipped"
                                ? "neutral"
                                : "warning"
                          }
                        >
                          {log.status}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="max-w-md truncate text-xs text-muted-foreground">
                      {log.message ?? "—"}
                    </TableCell>
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell colSpan={5} className="h-auto py-8 text-center text-muted-foreground">
                    No processing logs found.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>

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
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Intake</TableHead>
                <TableHead>Supplier</TableHead>
                <TableHead>Cost path</TableHead>
                <TableHead>AI model</TableHead>
                <TableHead numeric>Tokens in</TableHead>
                <TableHead numeric>Tokens out</TableHead>
                <TableHead numeric>Spend</TableHead>
                <TableHead numeric>Duration</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
                {jobs && jobs.items.length > 0 ? (
                  jobs.items.map((job) => (
                    <TableRow key={job.id}>
                      <TableCell className="font-mono text-xs text-muted-foreground">
                        {format(new Date(job.created_at), "dd MMM HH:mm")}
                      </TableCell>
                      <TableCell>
                        <Link
                          href={`/invoices/${job.intake_id}`}
                          className="font-mono text-xs text-primary hover:underline"
                        >
                          {job.intake_id.slice(0, 8)}
                        </Link>
                      </TableCell>
                      <TableCell className="max-w-[180px] truncate">
                        {job.supplier_name ?? "—"}
                      </TableCell>
                      <TableCell>
                        {/* The accent marks the free path — that is the product
                            working. Paid is the unremarkable case. */}
                        <ExtractionPathBadge path={job.extraction_path} />
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {job.ai_model_name ?? "—"}
                      </TableCell>
                      <TableCell numeric className="text-xs">
                        {job.tokens_input > 0 ? job.tokens_input.toLocaleString() : "—"}
                      </TableCell>
                      <TableCell numeric className="text-xs">
                        {job.tokens_output > 0 ? job.tokens_output.toLocaleString() : "—"}
                      </TableCell>
                      <TableCell numeric className="text-xs">
                        {job.estimated_cost_usd > 0
                          ? `$${job.estimated_cost_usd.toFixed(4)}`
                          : "—"}
                      </TableCell>
                      <TableCell numeric className="text-xs">
                        {job.extraction_duration_ms > 0
                          ? `${(job.extraction_duration_ms / 1000).toFixed(1)}s`
                          : "—"}
                      </TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell
                      colSpan={9}
                      className="h-auto py-8 text-center text-muted-foreground"
                    >
                      No extraction jobs found.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>

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
