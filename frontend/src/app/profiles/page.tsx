"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useProfiles, useDeleteProfile } from "@/hooks/use-profiles";
import { useModels } from "@/hooks/use-models";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/page-header";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { ErrorState, EmptyState, LoadingState } from "@/components/states";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { toast } from "sonner";
import { Plus, Search, MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { safeFormatDate } from "@/lib/format-date";
import { cn } from "@/lib/utils";
import type { ExtractionModel, SupplierProfile } from "@/types";

/**
 * Supplier list.
 *
 * Was a ragged grid of cards, each a cramped ten-field label/value list — a
 * table wearing a card costume, with none of a table's alignment or sorting.
 *
 * The important change is not the table. It is the **Template** column: 17 of
 * 20 active profiles have no template at all, covering 101 of 181 invoices, and
 * every one of those invoices pays full AI cost. Nothing in the old UI said so.
 * Template state is now the first thing on the row.
 */

type TemplateState = "ready" | "cutting" | "drifted" | "none";

const TEMPLATE_COPY: Record<TemplateState, { label: string; hint: string }> = {
  ready: { label: "Ready", hint: "Approved — invoices run free" },
  cutting: { label: "Cutting", hint: "Candidate in training" },
  drifted: { label: "Drifted", hint: "Validation failed — needs re-cutting" },
  none: { label: "None", hint: "No template: every invoice pays AI cost" },
};

function templateStateFor(
  profile: SupplierProfile,
  models: ExtractionModel[],
): TemplateState {
  const mine = models.filter((m) => m.supplier_profile_id === profile.profile_id);
  if (mine.length === 0) return "none";
  if (mine.some((m) => m.status === "approved")) return "ready";
  if (mine.some((m) => m.status === "failed_validation")) return "drifted";
  return "cutting";
}

function TemplateCell({ state }: { state: TemplateState }) {
  const { label, hint } = TEMPLATE_COPY[state];
  return (
    <span className="flex items-center gap-2" title={hint}>
      <Badge
        variant={
          state === "ready"
            ? "success"
            : state === "cutting"
              ? "cut"
              : state === "drifted"
                ? "warning"
                : "destructive"
        }
      >
        {label}
      </Badge>
    </span>
  );
}

type SortKey = "template" | "supplier" | "status";

const TEMPLATE_ORDER: Record<TemplateState, number> = {
  none: 0,
  drifted: 1,
  cutting: 2,
  ready: 3,
};

export default function ProfilesPage() {
  const router = useRouter();
  const { data: profiles, isLoading, isError, refetch } = useProfiles();
  // Per-profile template state needs the model list; one extra request, joined
  // client-side, rather than a new backend shape.
  const { data: modelPage } = useModels({ per_page: 100 });
  const deleteProfile = useDeleteProfile();
  const confirm = useConfirm();
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("template");

  const models = useMemo(() => modelPage?.items ?? [], [modelPage]);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = (profiles ?? [])
      .filter(
        (p) =>
          !q ||
          p.identity.canonical_name.toLowerCase().includes(q) ||
          p.profile_id.toLowerCase().includes(q) ||
          (p.identity.aliases ?? []).some((a) => a.toLowerCase().includes(q)),
      )
      .map((p) => ({ profile: p, template: templateStateFor(p, models) }));

    return list.sort((a, b) => {
      if (sort === "supplier")
        return a.profile.identity.canonical_name.localeCompare(
          b.profile.identity.canonical_name,
        );
      if (sort === "status") return a.profile.status.localeCompare(b.profile.status);
      // Default: the ones costing money first.
      const d = TEMPLATE_ORDER[a.template] - TEMPLATE_ORDER[b.template];
      return d !== 0
        ? d
        : a.profile.identity.canonical_name.localeCompare(b.profile.identity.canonical_name);
    });
  }, [profiles, models, search, sort]);

  // Only ACTIVE suppliers are a problem. A draft or training profile has no
  // template yet by definition — counting those would cry wolf.
  const atRisk = rows.filter(
    (r) => r.template === "none" && r.profile.status === "active",
  );
  const activeCount = rows.filter((r) => r.profile.status === "active").length;

  async function handleDelete(profile: SupplierProfile) {
    const ok = await confirm({
      title: `Delete ${profile.identity.canonical_name}?`,
      description:
        "This removes the supplier's extraction configuration. Invoices already processed are unaffected.",
      confirmLabel: "Delete supplier",
      variant: "destructive",
    });
    if (!ok) return;
    try {
      await deleteProfile.mutateAsync(profile.profile_id);
      toast.success(`${profile.identity.canonical_name} deleted.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not delete supplier.");
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Suppliers"
        description="How each supplier's invoices are read and delivered."
        actions={
          // One entry point. The system picks AI or manual authoring; the user
          // should not have to choose a mode before knowing anything.
          <Button variant="solid" render={<Link href="/profiles/new/assistant" />}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            Add supplier
          </Button>
        }
      />

      {/* The number that matters, stated rather than buried in a column */}
      {atRisk.length > 0 && (
        <p className="border-l-2 border-destructive bg-destructive/8 px-3 py-2 text-[0.8125rem] text-foreground">
          <strong>{atRisk.length}</strong> of {activeCount} live suppliers have no template —
          every invoice from them pays full AI cost. Drafts are not counted.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[16rem] flex-1 md:max-w-sm">
          <Search
            className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <Input
            className="h-8 pl-8"
            placeholder="Search supplier or alias…"
            aria-label="Search suppliers"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <span className="label-mono ml-auto">
          {rows.length} supplier{rows.length === 1 ? "" : "s"}
        </span>
      </div>

      {isLoading ? (
        <LoadingState rows={8} />
      ) : isError ? (
        <ErrorState what="supplier profiles" onRetry={() => refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState
          title={search ? `No supplier matches “${search}”.` : "No suppliers configured yet."}
          hint={
            search
              ? "Try the supplier's short name or one of its aliases."
              : "Add a supplier to tell Stencil how to read its invoices."
          }
        />
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>
                <button
                  type="button"
                  onClick={() => setSort("template")}
                  className={cn("uppercase", sort === "template" && "text-foreground")}
                >
                  Template
                </button>
              </TableHead>
              <TableHead>
                <button
                  type="button"
                  onClick={() => setSort("supplier")}
                  className={cn("uppercase", sort === "supplier" && "text-foreground")}
                >
                  Supplier
                </button>
              </TableHead>
              <TableHead>Profile ID</TableHead>
              <TableHead>
                <button
                  type="button"
                  onClick={() => setSort("status")}
                  className={cn("uppercase", sort === "status" && "text-foreground")}
                >
                  Status
                </button>
              </TableHead>
              <TableHead>Delivers to</TableHead>
              <TableHead>Updated</TableHead>
              <TableHead className="w-8" aria-label="Actions" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(({ profile, template }) => (
              <TableRow
                key={profile.profile_id}
                className="cursor-pointer"
                onClick={() => router.push(`/profiles/${profile.profile_id}`)}
              >
                <TableCell>
                  <TemplateCell state={template} />
                </TableCell>
                <TableCell>
                  <span className="block max-w-[220px] truncate font-medium">
                    {profile.identity.canonical_name}
                  </span>
                </TableCell>
                <TableCell>
                  <span className="font-mono text-xs text-muted-foreground">
                    {profile.profile_id}
                  </span>
                </TableCell>
                <TableCell>
                  <span className="text-muted-foreground capitalize">{profile.status}</span>
                </TableCell>
                <TableCell>
                  <span
                    className="block max-w-[220px] truncate font-mono text-xs text-muted-foreground"
                    title={profile.delivery?.output_path ?? undefined}
                  >
                    {profile.delivery?.output_path ?? "—"}
                  </span>
                </TableCell>
                <TableCell>
                  <span className="font-mono text-xs text-muted-foreground">
                    {safeFormatDate(
                      (profile.audit?.updated as { at?: string } | undefined)?.at ?? null,
                      "dd MMM yyyy",
                    )}
                  </span>
                </TableCell>
                <TableCell onClick={(e) => e.stopPropagation()}>
                  {/* Destructive actions are never ambient — 18 red trash icons
                      used to sit permanently on this screen. */}
                  <DropdownMenu>
                    <DropdownMenuTrigger
                      render={
                        <Button
                          variant="text"
                          size="icon-xs"
                          aria-label={`Actions for ${profile.identity.canonical_name}`}
                        >
                          <MoreHorizontal className="h-4 w-4" aria-hidden="true" />
                        </Button>
                      }
                    />
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem
                        onClick={() => router.push(`/profiles/${profile.profile_id}`)}
                      >
                        <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                        Edit
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => handleDelete(profile)}>
                        <Trash2 className="h-3.5 w-3.5 text-destructive" aria-hidden="true" />
                        Delete
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
