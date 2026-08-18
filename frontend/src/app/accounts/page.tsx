"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import {
  useAccounts,
  useAssignAccounts,
  useUnassignAccounts,
  useCreateAccount,
  useSyncAccounts,
  useAccountsSyncStatus,
} from "@/hooks/use-accounts";
import { useProfiles } from "@/hooks/use-profiles";
import { safeFormatDate } from "@/lib/format-date";
import { PageHeader } from "@/components/page-header";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Search,
  RefreshCw,
  ChevronRight,
  ChevronDown,
  Landmark,
  Plus,
} from "lucide-react";
import type { AccountRow, AccountState } from "@/types";

const STATE_BADGE: Record<AccountState, string> = {
  unmapped: "bg-warning/12 text-warning",
  mapped: "bg-success/12 text-success",
  conflict: "bg-destructive/12 text-destructive",
  missing: "bg-muted text-muted-foreground",
};

const STATE_FILTERS: (AccountState | "all")[] = ["all", "unmapped", "mapped", "conflict", "missing"];

export default function AccountsPage() {
  const { data, isLoading, isError } = useAccounts();
  const { data: profiles } = useProfiles();
  const assign = useAssignAccounts();
  const unassign = useUnassignAccounts();
  const syncMut = useSyncAccounts();
  const queryClient = useQueryClient();

  // Poll the sync status while a scan is running; reload the snapshot when it finishes.
  const [polling, setPolling] = useState(false);
  const syncStatus = useAccountsSyncStatus(polling || !!data?.syncing);
  const st = syncStatus.data;
  // Keep the local polling flag in sync with server state during render — React's
  // recommended alternative to a setState-in-effect. The two branches are mutually
  // exclusive on st.status, and each is guarded by the current value, so they
  // cannot ping-pong across re-renders.
  if (data?.syncing && st?.status !== "done" && st?.status !== "error" && !polling) {
    setPolling(true);
  } else if ((st?.status === "done" || st?.status === "error") && polling) {
    setPolling(false);
  }
  // Reload the accounts snapshot once a scan finishes.
  useEffect(() => {
    if (st?.status === "done") {
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
    }
  }, [st?.status, queryClient]);
  const isSyncing =
    syncMut.isPending ||
    st?.status === "queued" ||
    st?.status === "running" ||
    (!!data?.syncing && st?.status !== "done" && st?.status !== "error");
  const syncPct = st?.customers_total
    ? Math.round((100 * (st.customers_done ?? 0)) / st.customers_total)
    : 6;

  async function doSync() {
    try {
      await syncMut.mutateAsync();
      setPolling(true);
    } catch {
      toast.error("Could not start sync.");
    }
  }

  const [search, setSearch] = useState("");
  const [stateFilter, setStateFilter] = useState<AccountState | "all">("all");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [assignTo, setAssignTo] = useState<string>("");

  const profileName = (id: string) =>
    profiles?.find((p) => p.profile_id === id)?.identity.canonical_name ?? id;

  // Filter groups/rows by search + state.
  const groups = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (data?.groups ?? [])
      .map((g) => ({
        ...g,
        accounts: g.accounts.filter((a) => {
          if (stateFilter !== "all" && a.state !== stateFilter) return false;
          if (!q) return true;
          return (
            a.account.toLowerCase().includes(q) ||
            a.customer.toLowerCase().includes(q) ||
            a.inbound_path.toLowerCase().includes(q)
          );
        }),
      }))
      .filter((g) => g.accounts.length > 0);
  }, [data, search, stateFilter]);

  const toggleGroupExpand = (customer: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(customer)) next.delete(customer);
      else next.add(customer);
      return next;
    });

  const toggleRow = (path: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  const toggleGroupSelect = (rows: AccountRow[]) =>
    setSelected((prev) => {
      const next = new Set(prev);
      const paths = rows.map((r) => r.inbound_path);
      const allSelected = paths.every((p) => next.has(p));
      paths.forEach((p) => (allSelected ? next.delete(p) : next.add(p)));
      return next;
    });

  const clearSelection = () => setSelected(new Set());

  async function doAssign() {
    if (!assignTo || selected.size === 0) return;
    try {
      const res = await assign.mutateAsync({
        profile_id: assignTo,
        inbound_paths: [...selected],
      });
      const detached = (res as { detached_from?: string[] })?.detached_from ?? [];
      toast.success(
        `Assigned ${selected.size} account(s) to ${profileName(assignTo)}` +
          (detached.length ? ` (detached from ${detached.length} other profile(s))` : "")
      );
      clearSelection();
    } catch {
      toast.error("Failed to assign. An account may already be mapped elsewhere.");
    }
  }

  async function doUnassign() {
    if (selected.size === 0) return;
    try {
      await unassign.mutateAsync({ inbound_paths: [...selected] });
      toast.success(`Unassigned ${selected.size} account(s). Folders left on disk.`);
      clearSelection();
    } catch {
      toast.error("Failed to unassign.");
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Accounts"
        description={
          data
            ? `${data.total_accounts} accounts under ${data.scan_base} · ${data.unmapped} unmapped, ${data.conflict} conflict, ${data.missing} missing`
            : "Discover account folders and map them to profiles."
        }
        actions={
          <div className="flex items-center gap-3">
            {data?.synced_at && !isSyncing && (
              <span className="text-xs text-muted-foreground">
                Synced {safeFormatDate(data.synced_at, "MMM d HH:mm")}
              </span>
            )}
            <AddAccountDialog />
            <Button variant="outline" onClick={doSync} disabled={isSyncing}>
              <RefreshCw className={`h-4 w-4 ${isSyncing ? "animate-spin" : ""}`} />
              {isSyncing ? "Syncing…" : "Sync"}
            </Button>
          </div>
        }
      />

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative max-w-sm flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search customer, account, folder..."
            className="pl-9"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="flex gap-1">
          {STATE_FILTERS.map((s) => (
            <Button
              key={s}
              size="sm"
              variant={stateFilter === s ? "default" : "outline"}
              onClick={() => setStateFilter(s)}
              className="capitalize"
            >
              {s}
            </Button>
          ))}
        </div>
      </div>

      {/* Bulk-assign toolbar */}
      {selected.size > 0 && (
        <div className="sticky top-2 z-10 flex flex-wrap items-center gap-3 rounded-lg border bg-background/95 px-4 py-3 shadow-sm backdrop-blur">
          <span className="text-sm font-medium">{selected.size} selected</span>
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">Assign to</span>
            <Select value={assignTo || undefined} onValueChange={(v) => setAssignTo(v ?? "")}>
              <SelectTrigger className="min-w-52">
                <SelectValue placeholder="Select a profile">
                  {(id: string | null) => (id ? profileName(id) : "Select a profile")}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {(profiles ?? []).map((p) => (
                  <SelectItem key={p.profile_id} value={p.profile_id}>
                    {p.identity.canonical_name}{" "}
                    <span className="text-muted-foreground">({p.status})</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button onClick={doAssign} disabled={!assignTo || assign.isPending}>
              Assign
            </Button>
          </div>
          <Button variant="outline" onClick={doUnassign} disabled={unassign.isPending}>
            Unassign
          </Button>
          <Button variant="ghost" onClick={clearSelection} className="ml-auto">
            Clear
          </Button>
        </div>
      )}

      {/* Background scan progress — the disk walk runs in the worker, not here. */}
      {isSyncing && (
        <Card>
          <CardContent className="space-y-2 py-3">
            <div className="flex items-center justify-between text-sm">
              <span className="flex items-center gap-2 text-muted-foreground">
                <RefreshCw className="h-4 w-4 animate-spin" />
                Scanning account folders…
              </span>
              <span className="text-muted-foreground">
                {st?.accounts_found ?? 0} found
                {st?.customers_total ? ` · ${st.customers_done ?? 0}/${st.customers_total} customers` : ""}
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-sm bg-muted">
              <div
                className="h-full rounded-sm bg-primary transition-all duration-500"
                style={{ width: `${syncPct}%` }}
              />
            </div>
          </CardContent>
        </Card>
      )}

      {/* Content */}
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-12" />
          ))}
        </div>
      ) : isError ? (
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <p className="text-sm text-destructive">Failed to load accounts. Is the backend running?</p>
          </CardContent>
        </Card>
      ) : data?.never_synced && !isSyncing ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <Landmark className="mb-3 h-10 w-10 text-muted-foreground" />
            <p className="mb-3 text-sm text-muted-foreground">
              No snapshot yet — run a sync to discover account folders on disk.
            </p>
            <Button onClick={doSync} disabled={isSyncing}>
              <RefreshCw className="h-4 w-4" />
              Sync now
            </Button>
          </CardContent>
        </Card>
      ) : groups.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <Landmark className="mb-3 h-10 w-10 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              {search || stateFilter !== "all"
                ? "No accounts match the filter."
                : isSyncing
                  ? "Scanning…"
                  : "No account folders found under the scan directory."}
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {groups.map((g) => {
            const open = expanded.has(g.customer);
            const groupPaths = g.accounts.map((a) => a.inbound_path);
            const selectedInGroup = groupPaths.filter((p) => selected.has(p)).length;
            return (
              <Card key={g.customer} className="overflow-hidden">
                <button
                  className="flex w-full items-center gap-2 px-4 py-3 text-left hover:bg-muted/40"
                  onClick={() => toggleGroupExpand(g.customer)}
                >
                  {open ? (
                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                  )}
                  <span className="font-medium">{g.customer}</span>
                  <span className="text-xs text-muted-foreground">
                    {g.accounts.length} account{g.accounts.length === 1 ? "" : "s"}
                  </span>
                  {g.unmapped > 0 && (
                    <Badge variant="outline" className={STATE_BADGE.unmapped}>
                      {g.unmapped} unmapped
                    </Badge>
                  )}
                  {g.conflict > 0 && (
                    <Badge variant="outline" className={STATE_BADGE.conflict}>
                      {g.conflict} conflict
                    </Badge>
                  )}
                  {selectedInGroup > 0 && (
                    <span className="ml-auto text-xs text-muted-foreground">
                      {selectedInGroup} selected
                    </span>
                  )}
                </button>

                {open && (
                  <div className="border-t">
                    <div className="flex items-center gap-2 border-b bg-muted/20 px-4 py-1.5 text-xs">
                      <input
                        type="checkbox"
                        aria-label={`Select all in ${g.customer}`}
                        checked={selectedInGroup === groupPaths.length && groupPaths.length > 0}
                        onChange={() => toggleGroupSelect(g.accounts)}
                      />
                      <span className="text-muted-foreground">Select all</span>
                    </div>
                    {g.accounts.map((a) => (
                      <div
                        key={a.inbound_path}
                        className="flex items-center gap-3 px-4 py-2 text-sm hover:bg-muted/30 data-[selected=true]:bg-accent/40"
                        data-selected={selected.has(a.inbound_path)}
                      >
                        <input
                          type="checkbox"
                          aria-label={`Select ${a.account}`}
                          checked={selected.has(a.inbound_path)}
                          onChange={() => toggleRow(a.inbound_path)}
                        />
                        <span className="font-mono">{a.account}</span>
                        <span className="text-xs text-muted-foreground">{a.pdf_count} pdf</span>
                        <Badge variant="outline" className={STATE_BADGE[a.state]}>
                          {a.state}
                        </Badge>
                        {a.profile_ids.length > 0 && (
                          <span className="text-xs text-muted-foreground">
                            {a.profile_ids.map((id, i) => (
                              <span key={id}>
                                {i > 0 && ", "}
                                <Link href={`/profiles/${id}`} className="underline hover:text-foreground">
                                  {profileName(id)}
                                </Link>
                              </span>
                            ))}
                          </span>
                        )}
                        <span className="ml-auto truncate font-mono text-xs text-muted-foreground/70">
                          {a.inbound_path}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

function AddAccountDialog() {
  const { data: profiles } = useProfiles();
  const create = useCreateAccount();
  const [open, setOpen] = useState(false);
  const [customer, setCustomer] = useState("");
  const [account, setAccount] = useState("");
  const [profileId, setProfileId] = useState("");

  async function submit() {
    if (!customer.trim() || !account.trim() || !profileId) return;
    try {
      await create.mutateAsync({ profile_id: profileId, customer: customer.trim(), account: account.trim() });
      toast.success(`Created ${customer}/${account} and mapped it.`);
      setOpen(false);
      setCustomer("");
      setAccount("");
      setProfileId("");
    } catch {
      toast.error("Failed to create the account folder.");
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button variant="outline">
            <Plus className="h-4 w-4" />
            Add account
          </Button>
        }
      />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add account</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">
            Creates <span className="font-mono">&lt;customer&gt;/&lt;account&gt;/pdf</span> and{" "}
            <span className="font-mono">/xls</span> on disk and maps it to the profile.
          </p>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Customer folder</label>
            <Input value={customer} onChange={(e) => setCustomer(e.target.value)} placeholder="e.g. 82824706" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Account folder</label>
            <Input value={account} onChange={(e) => setAccount(e.target.value)} placeholder="e.g. A837737" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground">Profile</label>
            <Select value={profileId || undefined} onValueChange={(v) => setProfileId(v ?? "")}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select a profile">
                  {(id: string | null) =>
                    id
                      ? profiles?.find((p) => p.profile_id === id)?.identity.canonical_name ?? id
                      : "Select a profile"
                  }
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {(profiles ?? []).map((p) => (
                  <SelectItem key={p.profile_id} value={p.profile_id}>
                    {p.identity.canonical_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button onClick={submit} disabled={!customer.trim() || !account.trim() || !profileId || create.isPending}>
            Create & map
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
