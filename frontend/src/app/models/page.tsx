"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useModels, useDeleteModel, useBootstrapModel } from "@/hooks/use-models";
import { useProfiles } from "@/hooks/use-profiles";
import { modelDetailPath } from "@/lib/model-routes";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { safeFormatDate } from "@/lib/format-date";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ModelStatusBadge, ConfidenceBadge } from "@/components/status-badge";
import { DataTable, SortableHeader } from "@/components/data-table";
import { PageHeader } from "@/components/page-header";
import { type ColumnDef } from "@tanstack/react-table";
import { Search, Trash2, Plus, Wrench } from "lucide-react";
import { toast } from "sonner";
import type { ExtractionModel } from "@/types";

function NewModelDialog() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const { data: allProfiles } = useProfiles();
  const bootstrap = useBootstrapModel();
  // Any non-retired profile can have a model trained for it (a profile is only
  // "active" AFTER its model is approved — so we can't restrict to active here).
  const profiles = (allProfiles ?? []).filter((p) => p.status !== "retired");

  async function handlePick(profileId: string) {
    try {
      const model = await bootstrap.mutateAsync(profileId);
      setOpen(false);
      router.push(`${modelDetailPath(model.id)}?tab=training`);
    } catch {
      toast.error("Failed to create the model. Does the profile exist?");
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button size="sm" />}>
        <Plus className="h-4 w-4" />
        New model
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Train a new model</DialogTitle>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          Pick the supplier profile this model extracts for. You&apos;ll then upload
          sample invoices and train it.
        </p>
        <div className="max-h-72 overflow-y-auto rounded-md border divide-y">
          {profiles.map((p) => (
            <button
              key={p.profile_id}
              className="w-full text-left px-3 py-2 text-sm hover:bg-muted/50 disabled:opacity-50 flex items-center justify-between gap-2"
              disabled={bootstrap.isPending}
              onClick={() => handlePick(p.profile_id)}
            >
              <span>
                <span className="font-medium">{p.identity.canonical_name}</span>{" "}
                <span className="font-mono text-xs text-muted-foreground">{p.profile_id}</span>
              </span>
              <Badge variant="secondary" className="text-xs shrink-0">{p.status}</Badge>
            </button>
          ))}
          {profiles.length === 0 && (
            <p className="px-3 py-3 text-xs text-muted-foreground">
              No supplier profiles yet. Create one on the Profiles page first.
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

const baseColumns: ColumnDef<ExtractionModel>[] = [
  {
    accessorKey: "supplier",
    header: ({ column }) => <SortableHeader column={column} label="Supplier" />,
    cell: ({ row }) => (
      <span className="font-medium text-sm">{row.original.supplier}</span>
    ),
  },
  {
    accessorKey: "id",
    header: "Model ID",
    cell: ({ row }) => (
      <span className="font-mono text-xs text-muted-foreground">
        {row.original.id.length > 20
          ? `${row.original.id.slice(0, 20)}...`
          : row.original.id}
      </span>
    ),
  },
  {
    accessorKey: "status",
    header: "Status",
    cell: ({ row }) => <ModelStatusBadge status={row.original.status} />,
  },
  {
    accessorKey: "confidence",
    header: "Confidence",
    cell: ({ row }) => (
      <ConfidenceBadge confidence={row.original.confidence} />
    ),
  },
  {
    accessorKey: "times_used",
    header: "Used",
    cell: ({ row }) => (
      <span className="text-sm text-muted-foreground">
        {row.original.times_used}x
      </span>
    ),
  },
  {
    accessorKey: "output_type",
    header: "Type",
    cell: ({ row }) => (
      <Badge variant="secondary" className="text-xs">
        {row.original.output_type}
      </Badge>
    ),
  },
  {
    accessorKey: "created_by",
    header: "Created By",
    cell: ({ row }) => (
      <span className="text-sm text-muted-foreground">
        {row.original.created_by || "—"}
      </span>
    ),
  },
  {
    accessorKey: "created_at",
    header: ({ column }) => <SortableHeader column={column} label="Created" />,
    cell: ({ row }) => (
      <span className="text-sm text-muted-foreground">
        {safeFormatDate(row.original.created_at, "MMM d, yyyy")}
      </span>
    ),
  },
];

export default function ModelsPage() {
  const router = useRouter();
  const deleteModel = useDeleteModel();
  const confirm = useConfirm();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const { data, isLoading, isError } = useModels({
    page,
    per_page: 20,
    status: status || undefined,
    supplier: search || undefined,
  });

  async function handleDelete(model: ExtractionModel, event: React.MouseEvent) {
    event.stopPropagation();
    const ok = await confirm({
      title: `Permanently delete model ${model.id}?`,
      description: "This cannot be undone.",
      confirmLabel: "Delete",
      variant: "destructive",
    });
    if (!ok) return;
    try {
      await deleteModel.mutateAsync(model.id);
      toast.success("Model deleted.");
    } catch {
      toast.error("Failed to delete model.");
    }
  }

  const columns: ColumnDef<ExtractionModel>[] = [
    ...baseColumns,
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <Button
          variant="ghost"
          size="icon-sm"
          className="text-muted-foreground hover:text-destructive"
          disabled={deleteModel.isPending}
          onClick={(event) => handleDelete(row.original, event)}
          title="Delete model"
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <PageHeader
          title="Extraction Models"
          description="AI-generated extraction models. Candidate models must pass validation before they become trusted for reuse."
        />
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={() => router.push("/models/builder")}>
            <Wrench className="mr-2 h-4 w-4" />
            Build model
          </Button>
          <NewModelDialog />
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-4">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search by supplier..."
            aria-label="Search templates by supplier"
            className="pl-9"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <Select
          value={status}
          onValueChange={(v) => {
            setStatus(v === "all" ? "" : (v ?? ""));
            setPage(1);
          }}
        >
          <SelectTrigger className="w-[160px]">
            <SelectValue placeholder="All statuses" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            <SelectItem value="draft">Draft</SelectItem>
            <SelectItem value="candidate">Candidate</SelectItem>
            <SelectItem value="approved">Approved</SelectItem>
            <SelectItem value="retired">Retired</SelectItem>
            <SelectItem value="failed_validation">Failed Validation</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Table */}
      {isLoading ? (
        <Card>
          <CardContent className="pt-6">
            <div className="space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          </CardContent>
        </Card>
      ) : isError ? (
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-sm text-destructive">
              Failed to load models. Is the backend running?
            </p>
          </CardContent>
        </Card>
      ) : (data?.items ?? []).length === 0 && !search && !status ? (
        <Card>
          <CardContent className="pt-10 pb-10 flex flex-col items-center gap-3 text-center">
            <p className="text-sm text-muted-foreground max-w-md">
              No extraction models yet. Create one for a supplier profile, then upload
              sample invoices and train it — the model extracts future invoices of that
              layout with no AI cost.
            </p>
            <NewModelDialog />
          </CardContent>
        </Card>
      ) : (
        <DataTable
          columns={columns}
          data={data?.items ?? []}
          page={page}
          totalPages={data?.pages ?? 1}
          onPageChange={setPage}
          onRowClick={(model) => router.push(modelDetailPath(model.id))}
        />
      )}

    </div>
  );
}
