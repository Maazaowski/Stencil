"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { type ColumnDef } from "@tanstack/react-table";
import { startOfDay, subDays } from "date-fns";
import { safeFormatDate } from "@/lib/format-date";
import { useInvoices, useSupplierFacets } from "@/hooks/use-invoices";
import { DataTable, SortableHeader } from "@/components/data-table";
import {
  StatusBadge,
  ExtractionPathBadge,
  ReconciliationBadge,
} from "@/components/status-badge";
import { PageHeader } from "@/components/page-header";
import { WorkStatusLine } from "@/components/work-status-line";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Search, SearchX, Upload, X, SlidersHorizontal } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import Link from "next/link";
import { cn } from "@/lib/utils";
import type { InvoiceListItem } from "@/types";

const columns: ColumnDef<InvoiceListItem>[] = [
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <StatusBadge status={row.original.status} />,
  },
  {
    accessorKey: "supplier_name",
    header: ({ column }) => <SortableHeader column={column} label="Supplier" />,
    cell: ({ row }) =>
      row.original.supplier_name ? (
        <span className="block max-w-[190px] truncate font-medium">
          {row.original.supplier_name}
        </span>
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
  },
  {
    accessorKey: "original_filename",
    header: ({ column }) => <SortableHeader column={column} label="Invoice" />,
    // Telecom filenames differ in their TAIL (date/sequence), so truncating the
    // end — as the old table did — hid the only distinguishing part.
    cell: ({ row }) => (
      <span
        className="block max-w-[300px] truncate font-mono text-xs [direction:rtl] text-left"
        title={row.original.original_filename}
      >
        {row.original.original_filename}
      </span>
    ),
  },
  {
    id: "reconciliation",
    header: "Reconciled",
    cell: ({ row }) => (
      <ReconciliationBadge
        isReconciled={row.original.is_reconciled}
        variance={row.original.reconciliation_variance}
      />
    ),
  },
  {
    accessorKey: "extraction_path",
    header: "Cost",
    cell: ({ row }) =>
      row.original.extraction_path ? (
        <ExtractionPathBadge path={row.original.extraction_path} />
      ) : (
        <span className="text-muted-foreground">—</span>
      ),
  },
  {
    accessorKey: "page_count",
    header: ({ column }) => <SortableHeader column={column} label="Pages" />,
    cell: ({ row }) => (
      <span className="font-mono text-xs text-muted-foreground">
        {row.original.page_count}
      </span>
    ),
  },
  {
    accessorKey: "started_at",
    header: ({ column }) => <SortableHeader column={column} label="Started" />,
    cell: ({ row }) => (
      <span className="whitespace-nowrap font-mono text-xs text-muted-foreground">
        {safeFormatDate(
          row.original.started_at ?? row.original.created_at,
          "dd MMM HH:mm",
        )}
      </span>
    ),
  },
];

const statusChips = [
  { value: "received", label: "Received", dot: "bg-muted" },
  { value: "processing", label: "Processing", dot: "bg-primary/12" },
  { value: "completed", label: "Completed", dot: "bg-success/12" },
  { value: "completed_with_warnings", label: "Warnings", dot: "bg-warning/12" },
  { value: "failed", label: "Failed", dot: "bg-destructive/12" },
];

const FILTER_GROUPS = [
  {
    key: "output_type" as const,
    label: "Type",
    options: [
      { value: "standard", label: "Standard" },
      { value: "wireless", label: "Wireless" },
      { value: "time_and_material", label: "Time & Material" },
    ],
  },
  {
    key: "path" as const,
    label: "Extraction cost",
    options: [
      { value: "model", label: "Free — ran from a template" },
      { value: "ai", label: "Paid — AI extraction" },
      { value: "model_fallback_ai", label: "Fell back to AI" },
    ],
  },
  {
    key: "source" as const,
    label: "Source",
    options: [
      { value: "watcher", label: "Watcher" },
      { value: "upload", label: "Upload" },
      { value: "training", label: "Training" },
    ],
  },
  {
    key: "range" as const,
    label: "Age",
    options: [
      { value: "today", label: "Today" },
      { value: "7d", label: "Last 7 days" },
      { value: "30d", label: "Last 30 days" },
      { value: "90d", label: "Last 90 days" },
    ],
  },
];

function rangeToDateFrom(range: string): string | undefined {
  const today = startOfDay(new Date());
  switch (range) {
    case "today":
      return today.toISOString();
    case "7d":
      return subDays(today, 7).toISOString();
    case "30d":
      return subDays(today, 30).toISOString();
    case "90d":
      return subDays(today, 90).toISOString();
    default:
      return undefined;
  }
}

function InvoicesPageContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const q = searchParams.get("q") ?? "";
  const suppliersParam = searchParams.get("suppliers") ?? "";
  const selectedSuppliers = useMemo(
    () => suppliersParam.split(",").map((s) => s.trim()).filter(Boolean),
    [suppliersParam],
  );
  const status = searchParams.get("status") ?? "";
  const outputType = searchParams.get("output_type") ?? "";
  const path = searchParams.get("path") ?? "";
  const source = searchParams.get("source") ?? "";
  const range = searchParams.get("range") ?? "";
  const page = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);

  // Local state only for debounced text inputs; everything else lives in the URL.
  const [searchInput, setSearchInput] = useState(q);

  const { data: supplierFacets } = useSupplierFacets();

  const setParams = (updates: Record<string, string | null>) => {
    const params = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value) params.set(key, value);
      else params.delete(key);
    }
    if (!("page" in updates)) params.delete("page");
    // Shallow routing via the History API. `router.replace` silently no-ops when
    // it would REMOVE the last query param after a hard page load, which is why
    // clearing a filter did nothing on a freshly-loaded URL. This is the pattern
    // Next documents for URL-driven filter state, and it stays in sync with
    // useSearchParams. See next/dist/docs/01-app/02-guides/single-page-applications.md
    window.history.replaceState(null, "", `?${params.toString()}`);
  };

  useEffect(() => {
    if (searchInput === q) return;
    const t = setTimeout(() => {
      setParams({ q: searchInput || null });
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput]);

  const toggleSupplier = (name: string) => {
    const next = selectedSuppliers.includes(name)
      ? selectedSuppliers.filter((s) => s !== name)
      : [...selectedSuppliers, name];
    setParams({ suppliers: next.length ? next.join(",") : null });
  };

  const dateFrom = useMemo(() => rangeToDateFrom(range), [range]);

  const { data, isLoading, isError, refetch } = useInvoices({
    page,
    per_page: 20,
    search: q || undefined,
    suppliers: suppliersParam || undefined,
    status: status || undefined,
    output_type: outputType || undefined,
    extraction_path: path || undefined,
    date_from: dateFrom,
    intake_source: source || undefined,
  });

  const total = data?.total;
  const statusCounts = data?.status_counts ?? {};
  const allCount = Object.values(statusCounts).reduce((a, b) => a + b, 0);

  const filterValues: Record<string, string> = {
    output_type: outputType,
    path,
    source,
    range,
  };

  type ActiveFilter = { key: string; value: string; label: string; clear: () => void };

  const activeFilters: ActiveFilter[] = [
    q && {
      key: "q",
      value: q,
      label: `“${q}”`,
      clear: () => {
        setSearchInput("");
        setParams({ q: null });
      },
    },
    ...selectedSuppliers.map((name) => ({
      key: "suppliers",
      value: name,
      label: name,
      clear: () => toggleSupplier(name),
    })),
    ...FILTER_GROUPS.flatMap((g) => {
      const v = filterValues[g.key];
      if (!v) return [];
      const opt = g.options.find((o) => o.value === v);
      return [
        {
          key: g.key,
          value: v,
          label: opt?.label ?? v,
          clear: () => setParams({ [g.key]: null }),
        },
      ];
    }),
  ].filter(Boolean) as ActiveFilter[];

  const hasFilters = activeFilters.length > 0 || !!status;

  const clearAllFilters = () => {
    setSearchInput("");
    router.replace(pathname);
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Work"
        description="Everything flowing through the pipeline."
      />

      {/* Replaces the old dashboard: three figures, in priority order. */}
      <WorkStatusLine />

      {/* Status chips — only for states that actually have rows. "Received 0"
          and "Processing 0" were permanent dead controls. */}
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          onClick={() => setParams({ status: null })}
          aria-pressed={!status}
          className={cn(
            "flex h-7 items-center gap-1.5 rounded-sm border px-2 text-[0.8125rem] transition-colors",
            !status
              ? "border-primary bg-primary/10 text-primary"
              : "border-border text-muted-foreground hover:border-border-strong hover:text-foreground",
          )}
        >
          All
          <span className="font-mono text-xs opacity-70">{allCount}</span>
        </button>
        {statusChips
          .filter((chip) => (statusCounts[chip.value] ?? 0) > 0 || status === chip.value)
          .map((chip) => {
            const on = status === chip.value;
            return (
              <button
                key={chip.value}
                type="button"
                aria-pressed={on}
                onClick={() => setParams({ status: on ? null : chip.value })}
                className={cn(
                  "flex h-7 items-center gap-1.5 rounded-sm border px-2 text-[0.8125rem] transition-colors",
                  on
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:border-border-strong hover:text-foreground",
                )}
              >
                {chip.label}
                <span className="font-mono text-xs opacity-70">
                  {statusCounts[chip.value] ?? 0}
                </span>
              </button>
            );
          })}
      </div>

      {/* Search is always visible; the rest lives behind one menu. */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[15rem] flex-1 md:max-w-sm">
          <Search
            className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <Input
            className="h-8 pl-8"
            placeholder="Search filename…"
            aria-label="Search by filename"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>

        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button variant="hairline" size="sm">
                <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
                Filters
                {activeFilters.length > 0 && (
                  <span className="ml-0.5 font-mono text-xs text-primary">
                    {activeFilters.length}
                  </span>
                )}
              </Button>
            }
          />
          <DropdownMenuContent align="start" className="w-64">
            <div className="px-2 py-1.5">
              <p className="label-mono mb-1">Supplier</p>
              <div className="max-h-40 overflow-y-auto">
                {(supplierFacets?.suppliers ?? []).length === 0 ? (
                  <p className="px-1 py-1 text-xs text-muted-foreground">No suppliers yet</p>
                ) : (
                  supplierFacets?.suppliers.map((name) => (
                    <DropdownMenuCheckboxItem
                      key={name}
                      checked={selectedSuppliers.includes(name)}
                      onCheckedChange={() => toggleSupplier(name)}
                    >
                      {name}
                    </DropdownMenuCheckboxItem>
                  ))
                )}
              </div>
            </div>

            {FILTER_GROUPS.map((group) => (
              <div key={group.key} className="border-t border-border px-2 py-1.5">
                <p className="label-mono mb-1">{group.label}</p>
                {group.options.map((opt) => (
                  <DropdownMenuCheckboxItem
                    key={opt.value}
                    checked={filterValues[group.key] === opt.value}
                    onCheckedChange={() =>
                      setParams({
                        [group.key]: filterValues[group.key] === opt.value ? null : opt.value,
                      })
                    }
                  >
                    {opt.label}
                  </DropdownMenuCheckboxItem>
                ))}
              </div>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        {/* Applied filters, each removable. Nothing shows when nothing applies. */}
        {activeFilters.map((f) => (
          <button
            key={f.key + f.value}
            type="button"
            onClick={f.clear}
            className="flex h-7 items-center gap-1 rounded-sm border border-primary/40 bg-primary/10 px-2 text-[0.8125rem] text-primary transition-colors hover:bg-primary/16"
            aria-label={`Remove filter ${f.label}`}
          >
            {f.label}
            <X className="h-3 w-3" aria-hidden="true" />
          </button>
        ))}

        {activeFilters.length > 1 && (
          <Button variant="text" size="sm" onClick={clearAllFilters}>
            Clear all
          </Button>
        )}

        <span className="label-mono ml-auto">{total ?? 0} invoices</span>
      </div>


      {/* Table */}
      {isLoading ? (
        <LoadingState rows={10} />
      ) : isError ? (
        <ErrorState what="the invoice queue" onRetry={() => refetch()} />
      ) : (
        <DataTable
          columns={columns}
          data={data?.items ?? []}
          page={page}
          totalPages={data?.pages ?? 1}
          onPageChange={(p) => setParams({ page: String(p) })}
          onRowClick={(row) => router.push(`/invoices/${row.id}`)}
          // Phone triage: the four things that decide whether this invoice needs
          // you. The other seven columns are desk work.
          mobileRow={(row) => (
            <div className="flex flex-col gap-1">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[0.8125rem] font-medium">
                  {row.supplier_name ?? "Unknown supplier"}
                </span>
                <StatusBadge status={row.status} />
              </div>
              <span className="truncate font-mono text-[0.6875rem] text-muted-foreground">
                {row.original_filename}
              </span>
              <div className="flex items-center gap-3 font-mono text-[0.6875rem]">
                <ReconciliationBadge
                  isReconciled={row.is_reconciled}
                  variance={row.reconciliation_variance}
                />
                {row.extraction_path && <ExtractionPathBadge path={row.extraction_path} />}
              </div>
            </div>
          )}
          emptyState={
            hasFilters ? (
              <EmptyState
                title="No invoices match these filters."
                hint="Widen the date range, or clear a filter to see more."
                action={
                  <Button variant="hairline" size="sm" onClick={clearAllFilters}>
                    <SearchX className="h-3.5 w-3.5" aria-hidden="true" />
                    Clear filters
                  </Button>
                }
                className="border-0"
              />
            ) : (
              <EmptyState
                title="Nothing has come through yet."
                hint="Drop a PDF into a watched supplier folder, or upload one by hand."
                action={
                  <Button variant="solid" size="sm" render={<Link href="/upload" />}>
                    <Upload className="h-3.5 w-3.5" aria-hidden="true" />
                    Upload an invoice
                  </Button>
                }
                className="border-0"
              />
            )
          }
        />
      )}
    </div>
  );
}

export default function InvoicesPage() {
  return (
    <Suspense>
      <InvoicesPageContent />
    </Suspense>
  );
}
