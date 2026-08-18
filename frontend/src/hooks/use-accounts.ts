import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { AccountsResponse, AccountsSyncStatus } from "@/types";

export function useAccounts() {
  return useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.get<AccountsResponse>("/accounts"),
  });
}

// Progress of the background folder scan; polls while a sync is queued/running.
export function useAccountsSyncStatus(enabled: boolean) {
  return useQuery({
    queryKey: ["accounts-sync-status"],
    queryFn: () => api.get<AccountsSyncStatus>("/accounts/sync/status"),
    enabled,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "queued" || s === "running" ? 1500 : false;
    },
  });
}

export function useSyncAccounts() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ status: string }>("/accounts/sync"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounts-sync-status"] });
    },
  });
}

// Assigning/creating/unassigning changes a profile's account mapping, so both
// the accounts list and the profiles list are invalidated on success.
function useAccountMutation<T>(mutationFn: (vars: T) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["accounts"] });
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
    },
  });
}

export function useAssignAccounts() {
  return useAccountMutation((vars: { profile_id: string; inbound_paths: string[] }) =>
    api.post("/accounts/assign", vars)
  );
}

export function useUnassignAccounts() {
  return useAccountMutation((vars: { inbound_paths: string[] }) =>
    api.post("/accounts/unassign", vars)
  );
}

export function useCreateAccount() {
  return useAccountMutation((vars: { profile_id: string; customer: string; account: string }) =>
    api.post("/accounts/create", vars)
  );
}
