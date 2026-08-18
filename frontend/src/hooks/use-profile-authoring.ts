import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { AuthoringJobStart, AuthoringSessionState } from "@/types";

const BASE_URL = "/api/v1";

/** Poll the live session state (invoices + extraction progress, draft, previews). */
export function useAuthoringSession(sessionId: string | null) {
  return useQuery({
    queryKey: ["authoring", sessionId],
    queryFn: () => api.get<AuthoringSessionState>(`/profiles/authoring/sessions/${sessionId}`),
    enabled: !!sessionId,
    // Poll while any uploaded invoice is still extracting in the worker.
    refetchInterval: (query) => {
      if (query.state.data?.status === "running") return 2500;
      const activeJobs = query.state.data?.jobs?.some((j) => j.status === "queued" || j.status === "running");
      if (activeJobs) return 2500;
      const invoices = query.state.data?.invoices ?? [];
      const pending = invoices.some((i) => i.extraction_status === "pending");
      return pending ? 2500 : false;
    },
  });
}

export function useCreateAuthoringSession() {
  return useMutation({
    mutationFn: (body: {
      supplier_name?: string;
      output_spec_id?: string;
      field_schema_id?: string;
      source_profile_id?: string;
      category_override?: "standard" | "wireless" | "time_and_material";
    }) => api.post<AuthoringSessionState>("/profiles/authoring/sessions", body),
  });
}

export function useUploadAuthoringBlueprint() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ sessionId, file }: { sessionId: string; file: File }) => {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${BASE_URL}/profiles/authoring/sessions/${sessionId}/blueprints`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) throw new Error(`Blueprint upload failed: ${res.statusText}`);
      return res.json() as Promise<{ blueprint_id: string; status: string }>;
    },
    onSuccess: (_data, { sessionId }) => {
      queryClient.invalidateQueries({ queryKey: ["authoring", sessionId] });
    },
  });
}

export function useDeleteAuthoringBlueprint() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, blueprintId }: { sessionId: string; blueprintId: string }) =>
      api.delete<{ blueprint_id: string; status: string }>(
        `/profiles/authoring/sessions/${sessionId}/blueprints/${blueprintId}`,
      ),
    onSuccess: (_data, { sessionId }) => {
      queryClient.invalidateQueries({ queryKey: ["authoring", sessionId] });
    },
  });
}

export function useDiscoverAuthoring(sessionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<AuthoringJobStart>(
      `/profiles/authoring/sessions/${sessionId}/discover`,
    ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["authoring", sessionId] }),
  });
}

/**
 * Upload one sample invoice (PDF) plus an optional XLS/XLSX blueprint. Built with
 * raw FormData because the shared api.upload helper only sends a single "file".
 */
export function useUploadAuthoringInvoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ sessionId, file, expected }: { sessionId: string; file: File; expected?: File }) => {
      const form = new FormData();
      form.append("file", file);
      if (expected) form.append("expected", expected);
      const res = await fetch(`${BASE_URL}/profiles/authoring/sessions/${sessionId}/invoices`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) throw new Error(`Upload failed: ${res.statusText}`);
      return res.json() as Promise<{ invoice_id: string; status: string }>;
    },
    onSuccess: (_data, { sessionId }) => {
      queryClient.invalidateQueries({ queryKey: ["authoring", sessionId] });
    },
  });
}

export function useDeleteAuthoringInvoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, invoiceId }: { sessionId: string; invoiceId: string }) =>
      api.delete<{ invoice_id: string; status: string }>(
        `/profiles/authoring/sessions/${sessionId}/invoices/${invoiceId}`,
      ),
    onSuccess: (_data, { sessionId }) => {
      queryClient.invalidateQueries({ queryKey: ["authoring", sessionId] });
    },
  });
}

/**
 * Send a chat message and queue an authoring turn. The live session query polls
 * queued/running jobs and refreshes previews when the worker finishes.
 */
export function useSendAuthoringMessage(sessionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (message: string): Promise<AuthoringJobStart> => {
      const start = await api.post<AuthoringJobStart>(
        `/profiles/authoring/sessions/${sessionId}/messages`,
        { message },
      );
      if (!start.job_id) throw new Error("Could not queue the authoring turn");
      return start;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["authoring", sessionId] });
    },
  });
}

/**
 * Re-run AI extraction on the cached samples with the current draft profile, so
 * refinements that need the model to re-read the PDF take effect. Runs in the
 * worker; the live session query polls until previews are refreshed.
 */
export function useReextractAuthoring(sessionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (invoiceId?: string): Promise<AuthoringJobStart> => {
      const start = await api.post<AuthoringJobStart>(
        `/profiles/authoring/sessions/${sessionId}/reextract`,
        { invoice_id: invoiceId ?? null },
      );
      if (!start.job_id) throw new Error("Could not queue re-extraction");
      return start;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["authoring", sessionId] });
    },
  });
}

/** Stop queued work and terminate the worker process holding any in-flight AI request. */
export function useCancelAuthoring(sessionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ status: "cancelled"; cancelled_jobs: number }>(
      `/profiles/authoring/sessions/${sessionId}/cancel`,
    ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["authoring", sessionId] });
    },
  });
}

export function useFinalizeAuthoringSession(sessionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { profile_id: string; layout_description?: string }) =>
      api.post<{ profile_id: string; status: string }>(
        `/profiles/authoring/sessions/${sessionId}/finalize`,
        body,
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
      queryClient.invalidateQueries({ queryKey: ["authoring", sessionId] });
    },
  });
}
