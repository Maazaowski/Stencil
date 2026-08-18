"use client";

import { useState } from "react";
import { format, subMonths } from "date-fns";
import type { MonthlyCostPoint } from "@/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/page-header";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useDashboardStats,
  useVolumeData,
  useCostData,
  useMonthlyStats,
  useCostByMonth,
  useCostByModel,
  useCostBySupplier,
} from "@/hooks/use-dashboard";
import {
  FileText,
  CheckCircle2,
  AlertTriangle,
  DollarSign,
  Zap,
  Cpu,
  Building2,
  GraduationCap,
  Tag,
} from "lucide-react";
import {
  BarChart,
  Bar,
  Cell,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
} from "recharts";

// ── Stat Card ──────────────────────────────────────────────

function StatCard({
  title,
  value,
  icon: Icon,
  description,
  isLoading,
}: {
  title: string;
  value: string | number;
  icon: React.ComponentType<{ className?: string }>;
  description?: string;
  isLoading?: boolean;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {title}
        </CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-8 w-24" />
        ) : (
          <>
            <div className="text-2xl font-bold">{value}</div>
            {description && (
              <p className="text-xs text-muted-foreground mt-1">
                {description}
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── Volume Chart ───────────────────────────────────────────

function VolumeChart() {
  const { data, isLoading } = useVolumeData(30);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Invoice Volume (30 days)</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-[250px] w-full" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Invoice Volume (30 days)</CardTitle>
      </CardHeader>
      <CardContent>
        {data && data.length > 0 ? (
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
              <XAxis
                dataKey="date"
                className="text-xs"
                tickFormatter={(v) => v.slice(5)} // MM-DD
              />
              <YAxis className="text-xs" allowDecimals={false} />
              <RechartsTooltip />
              <Bar
                dataKey="count"
                fill="var(--chart-1)"
                radius={[4, 4, 0, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-[250px] items-center justify-center text-sm text-muted-foreground">
            No volume data yet. Process some invoices to see trends.
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Cost Trend Chart ───────────────────────────────────────

function CostChart() {
  const { data, isLoading } = useCostData(30);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">AI Cost Trend (30 days)</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-[250px] w-full" />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">AI Cost Trend (30 days)</CardTitle>
      </CardHeader>
      <CardContent>
        {data && data.length > 0 ? (
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
              <XAxis
                dataKey="date"
                className="text-xs"
                tickFormatter={(v) => v.slice(5)}
              />
              <YAxis className="text-xs" tickFormatter={(v) => `$${v}`} />
              <RechartsTooltip
                formatter={(value) => [`$${Number(value).toFixed(4)}`, "Cost"]}
              />
              <Line
                type="monotone"
                dataKey="cost"
                stroke="var(--chart-1)"
                strokeWidth={2}
                dot={{ r: 3 }}
              />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-[250px] items-center justify-center text-sm text-muted-foreground">
            No cost data yet. AI extractions will show cost trends here.
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Monthly Usage ──────────────────────────────────────────

function usd(value: number | undefined | null): string {
  return `$${(value ?? 0).toFixed(2)}`;
}

// Last 12 months, current month first, as { value: "YYYY-MM", label: "July 2026" }.
const MONTH_OPTIONS = Array.from({ length: 12 }, (_, i) => {
  const d = subMonths(new Date(), i);
  return { value: format(d, "yyyy-MM"), label: format(d, "MMMM yyyy") };
});

function MonthlyTrendChart({
  month,
  onSelectMonth,
}: {
  month: string;
  onSelectMonth: (m: string) => void;
}) {
  const { data, isLoading } = useCostByMonth(12);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">AI Cost by Month (12 months)</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-[220px] w-full" />
        ) : data && data.some((d) => d.cost > 0) ? (
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
              <XAxis
                dataKey="month"
                className="text-xs"
                tickFormatter={(v) => v.slice(5)} // MM
              />
              <YAxis className="text-xs" tickFormatter={(v) => `$${v}`} />
              <RechartsTooltip
                formatter={(value) => [`$${Number(value).toFixed(2)}`, "Cost"]}
              />
              <Bar
                dataKey="cost"
                radius={[4, 4, 0, 0]}
                cursor="pointer"
                onClick={(d) => {
                  const clicked = (d?.payload as MonthlyCostPoint | undefined)?.month;
                  if (clicked) onSelectMonth(clicked);
                }}
              >
                {data.map((entry) => (
                  <Cell
                    key={entry.month}
                    fill={
                      entry.month === month
                        ? "var(--chart-2)"
                        : "var(--chart-1)"
                    }
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-[220px] items-center justify-center text-sm text-muted-foreground">
            No AI spend recorded yet.
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function CostByModelTable({ month }: { month: string }) {
  const { data, isLoading } = useCostByModel(month);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <Cpu className="h-4 w-4 text-muted-foreground" />
          Cost by AI Model
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-[160px] w-full" />
        ) : data && data.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Model</TableHead>
                <TableHead className="text-right">Calls</TableHead>
                <TableHead className="text-right">Cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((row) => (
                <TableRow key={row.model}>
                  <TableCell className="font-medium">{row.model}</TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {row.calls}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {usd(row.cost)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <div className="flex h-[120px] items-center justify-center text-sm text-muted-foreground">
            No AI spend this month.
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function CostBySupplierTable({ month }: { month: string }) {
  const { data, isLoading } = useCostBySupplier(month);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center gap-2">
          <Building2 className="h-4 w-4 text-muted-foreground" />
          Cost by Supplier / Account
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-[160px] w-full" />
        ) : data && data.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Supplier / Account</TableHead>
                <TableHead className="text-right">Calls</TableHead>
                <TableHead className="text-right">Cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((row, i) => (
                <TableRow key={`${row.supplier_profile_id ?? "unattributed"}-${i}`}>
                  <TableCell className="font-medium">
                    {row.account_label ||
                      row.supplier_profile_id || (
                        <span className="text-muted-foreground">
                          Model authoring / unattributed
                        </span>
                      )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {row.calls}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {usd(row.cost)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <div className="flex h-[120px] items-center justify-center text-sm text-muted-foreground">
            No AI spend this month.
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function MonthlyUsageSection() {
  const [month, setMonth] = useState(format(new Date(), "yyyy-MM"));
  const { data: m, isLoading } = useMonthlyStats(month);

  const totalPaths = (m?.ai_path_count ?? 0) + (m?.model_path_count ?? 0);
  const pctZero = totalPaths > 0 ? ((m?.model_path_count ?? 0) / totalPaths) * 100 : 0;
  const avgAiCost =
    (m?.ai_path_count ?? 0) > 0 ? (m?.extraction_cost ?? 0) / (m?.ai_path_count ?? 1) : null;
  const estSaved = avgAiCost !== null ? avgAiCost * (m?.model_path_count ?? 0) : null;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-heading font-semibold">Monthly Usage</h2>
          <p className="text-sm text-muted-foreground">
            AI cost and volume for the selected month.
          </p>
        </div>
        <Select
          value={month}
          onValueChange={(v) => setMonth(v as string)}
          items={Object.fromEntries(MONTH_OPTIONS.map((o) => [o.value, o.label]))}
        >
          <SelectTrigger className="w-[170px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {MONTH_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <MonthlyTrendChart month={month} onSelectMonth={setMonth} />

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <StatCard
          title="Total AI Cost"
          value={usd(m?.total_cost)}
          icon={DollarSign}
          description={`${m?.ai_calls ?? 0} AI calls`}
          isLoading={isLoading}
        />
        <StatCard
          title="Invoice Extraction"
          value={usd(m?.extraction_cost)}
          icon={FileText}
          description="Production invoice extraction"
          isLoading={isLoading}
        />
        <StatCard
          title="Classification"
          value={usd(m?.classification_cost)}
          icon={Tag}
          description="Supplier / layout classification"
          isLoading={isLoading}
        />
        <StatCard
          title="Model Training"
          value={usd(m?.training_cost)}
          icon={GraduationCap}
          description="Authoring & training models"
          isLoading={isLoading}
        />
        <StatCard
          title="Invoices Processed"
          value={m?.invoices_processed ?? 0}
          icon={CheckCircle2}
          description={`${m?.invoices_completed ?? 0} completed, ${m?.invoices_failed ?? 0} failed`}
          isLoading={isLoading}
        />
        <StatCard
          title="Ran at $0 (model path)"
          value={`${pctZero.toFixed(0)}%`}
          icon={Zap}
          description={
            estSaved !== null
              ? `Est. AI spend avoided: ${usd(estSaved)}`
              : "No AI extractions this month"
          }
          isLoading={isLoading}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <CostByModelTable month={month} />
        <CostBySupplierTable month={month} />
      </div>
    </div>
  );
}

export default function InsightsPage() {
  const { data: stats, isLoading, isError } = useDashboardStats();

  if (isError) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Dashboard"
          description="Overview of your invoice extraction pipeline."
        />
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 text-destructive">
              <AlertTriangle className="h-5 w-5" />
              <p>
                Unable to connect to the backend API. Make sure the FastAPI
                server is running on port 8000.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Insights"
        description="Cost and volume across the extraction pipeline."
      />

      {/* Stat Cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="Total Invoices"
          value={stats?.total_invoices ?? 0}
          icon={FileText}
          description={`${stats?.completed_invoices ?? 0} completed, ${stats?.failed_invoices ?? 0} failed`}
          isLoading={isLoading}
        />
        <StatCard
          title="Reconciled"
          value={`${(stats?.success_rate ?? 0).toFixed(1)}%`}
          icon={CheckCircle2}
          description={`${stats?.reconciled_jobs ?? 0} of ${stats?.scored_jobs ?? 0} extractions balance to the invoice total`}
          isLoading={isLoading}
        />
        <StatCard
          title="Needs Review"
          value={stats?.pending_exceptions ?? 0}
          icon={AlertTriangle}
          description={`${stats?.failed_invoices ?? 0} failed, ${stats?.completed_with_warnings ?? 0} delivered with warnings`}
          isLoading={isLoading}
        />
        <StatCard
          title="AI Cost (Total)"
          value={`$${(stats?.total_ai_cost ?? 0).toFixed(2)}`}
          icon={DollarSign}
          description={`${stats?.ai_path_count ?? 0} AI extractions`}
          isLoading={isLoading}
        />
      </div>

      {/*
        AI cost split by purpose. The total is NOT repeated here — it already
        appears in the row above, and rendering the same figure twice on one
        screen under two labels made both read as unreliable.
      */}
      <div className="grid gap-4 md:grid-cols-2">
        <StatCard
          title="Invoice Extraction"
          value={`$${(stats?.extraction_cost ?? 0).toFixed(2)}`}
          icon={DollarSign}
          description="Spent extracting production invoices"
          isLoading={isLoading}
        />
        <StatCard
          title="Template Training"
          value={`$${(stats?.model_training_cost ?? 0).toFixed(2)}`}
          icon={DollarSign}
          description="Spent cutting and validating templates"
          isLoading={isLoading}
        />
      </div>

      {/* Charts */}
      <div className="grid gap-4 lg:grid-cols-2">
        <VolumeChart />
        <CostChart />
      </div>

      {/* Monthly usage */}
      <MonthlyUsageSection />
    </div>
  );
}
