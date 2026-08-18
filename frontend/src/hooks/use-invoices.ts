import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  ExtractionPreview,
  IntakeRecord,
  IntakeDetail,
  InvoiceListResponse,
  MessageResponse,
} from "@/types";

export function useInvoices(params?: {
  page?: number;
  per_page?: number;
  status?: string;
  supplier?: string;
  suppliers?: string;
  search?: string;
  output_type?: string;
  extraction_path?: string;
  date_from?: string;
  date_to?: string;
  intake_source?: string;
  fingerprint?: string;
  pollWhileProcessing?: boolean;
}) {
  return useQuery({
    queryKey: ["invoices", params],
    queryFn: () =>
      api.get<InvoiceListResponse>("/invoices", {
        page: params?.page ?? 1,
        per_page: params?.per_page ?? 20,
        status: params?.status,
        supplier: params?.supplier,
        suppliers: params?.suppliers,
        search: params?.search,
        output_type: params?.output_type,
        extraction_path: params?.extraction_path,
        date_from: params?.date_from,
        date_to: params?.date_to,
        intake_source: params?.intake_source,
        fingerprint: params?.fingerprint,
      }),
    placeholderData: (prev) => prev,
    refetchInterval: (query) => {
      if (params?.pollWhileProcessing === false) return false;
      const data = query.state.data;
      const items = data?.items ?? [];
      const counts = data?.status_counts ?? {};
      const hasActiveInList = items.some(
        (item) => item.status === "processing" || item.status === "received"
      );
      const hasActiveGlobally =
        (counts.processing ?? 0) + (counts.received ?? 0) > 0;
      if (hasActiveInList || hasActiveGlobally) return 3000;
      // Keep polling while idle so watcher drops and uploads appear without refresh.
      return 5000;
    },
  });
}

export function useInvoice(intakeId: string) {
  return useQuery({
    queryKey: ["invoices", intakeId],
    queryFn: () => api.get<IntakeDetail>(`/invoices/${intakeId}`),
    enabled: !!intakeId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (
        status === "completed" ||
        status === "completed_with_warnings" ||
        status === "failed"
      ) {
        return false;
      }
      return 3000;
    },
  });
}

/** Tabular preview of a processed invoice's delivered output. */
export function useInvoicePreview(intakeId: string, enabled = true) {
  return useQuery({
    queryKey: ["invoices", intakeId, "preview"],
    queryFn: () => api.get<ExtractionPreview>(`/invoices/${intakeId}/preview`),
    enabled: enabled && !!intakeId,
    retry: false,
  });
}

export function useUploadInvoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) =>
      api.upload<IntakeRecord>("/invoices/upload", file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

export function useRejectInvoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (intakeId: string) =>
      api.post<MessageResponse>(`/invoices/${intakeId}/reject`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
      queryClient.invalidateQueries({ queryKey: ["models"] });
    },
  });
}

export function useDeleteInvoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (intakeId: string) =>
      api.delete<MessageResponse>(`/invoices/${intakeId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
  });
}

export function useCancelInvoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (intakeId: string) =>
      api.post<MessageResponse>(`/invoices/${intakeId}/cancel`),
    onSuccess: (_data, intakeId) => {
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
      queryClient.invalidateQueries({ queryKey: ["invoices", intakeId] });
    },
  });
}

export function useSupplierFacets() {
  return useQuery({
    queryKey: ["invoices", "facets", "suppliers"],
    queryFn: () => api.get<{ suppliers: string[] }>("/invoices/facets/suppliers"),
  });
}

export function usePurgeAllInvoices() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<MessageResponse>("/invoices/purge"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

/** True while any intake is queued or processing — drives dashboard polling. */
export function useHasActiveProcessing() {
  return useQuery({
    queryKey: ["invoices", "active-processing"],
    queryFn: () => api.get<InvoiceListResponse>("/invoices", { page: 1, per_page: 1 }),
    refetchInterval: 5000,
    select: (data) =>
      (data.status_counts.processing ?? 0) + (data.status_counts.received ?? 0) > 0,
  });
}
