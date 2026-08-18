import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  ExtractionPreview,
  SupplierProfile,
  MessageResponse,
  ProfileSummary,
  SimpleExtractionHints,
  TrainingStatus,
  TrainingUploadResponse,
} from "@/types";

type PreviewJob = {
  status: "pending" | "done" | "error";
  job_id?: string;
  preview?: ExtractionPreview;
  detail?: string;
};

/**
 * AI-extract a sample PDF under a profile and return its deliverable preview.
 * The extraction runs in the worker (it calls OpenAI and can take minutes), so
 * we start a job and poll until it finishes — a single long request would be
 * killed by HTTP proxy timeouts.
 */
export function useProfilePreview(profileId: string) {
  return useMutation({
    mutationFn: async (file: File): Promise<ExtractionPreview> => {
      const start = await api.upload<PreviewJob>(`/profiles/${profileId}/preview`, file);
      const jobId = start.job_id;
      if (!jobId) throw new Error("Could not start preview");
      const deadline = Date.now() + 6 * 60_000; // generous: big bills take minutes
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 2000));
        const job = await api.get<PreviewJob>(`/profiles/preview/${jobId}`);
        if (job.status === "done" && job.preview) return job.preview;
        if (job.status === "error") {
          throw new Error(job.detail || "Preview extraction failed");
        }
      }
      throw new Error("Preview timed out — try a smaller sample or check the worker.");
    },
  });
}

export function useProfiles() {
  return useQuery({
    queryKey: ["profiles"],
    queryFn: () => api.get<SupplierProfile[]>("/profiles"),
  });
}

export function useActiveProfiles() {
  return useQuery({
    queryKey: ["profiles", "active"],
    queryFn: () => api.get<ProfileSummary[]>("/profiles/active"),
  });
}

export function useProfile(profileId: string) {
  return useQuery({
    queryKey: ["profiles", profileId],
    queryFn: () => api.get<SupplierProfile>(`/profiles/${profileId}`),
    enabled: !!profileId,
  });
}

export function useCreateProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (profile: SupplierProfile) =>
      api.post<ProfileSummary>("/profiles", profile),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
    },
  });
}

export function useUpdateProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ profileId, profile }: { profileId: string; profile: SupplierProfile }) =>
      api.put<ProfileSummary>(`/profiles/${profileId}`, profile),
    onSuccess: (_data, { profileId }) => {
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
      queryClient.invalidateQueries({ queryKey: ["profiles", profileId] });
    },
  });
}

export function useDeleteProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (profileId: string) =>
      api.delete<MessageResponse>(`/profiles/${profileId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
    },
  });
}

// ── Simple hints ──────────────────────────────────────────

export function useSimpleHints(profileId: string) {
  return useQuery({
    queryKey: ["profiles", profileId, "simple-hints"],
    queryFn: () => api.get<SimpleExtractionHints>(`/profiles/${profileId}/simple-hints`),
    enabled: !!profileId,
  });
}

export function useUpdateSimpleHints() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ profileId, hints }: { profileId: string; hints: SimpleExtractionHints }) =>
      api.put<SupplierProfile>(`/profiles/${profileId}/simple-hints`, hints),
    onSuccess: (_data, { profileId }) => {
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
      queryClient.invalidateQueries({ queryKey: ["profiles", profileId] });
    },
  });
}

// ── Lifecycle + training workflow ─────────────────────────

export function useStartTraining() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ profileId, acknowledgeWarnings = false }: { profileId: string; acknowledgeWarnings?: boolean }) =>
      api.post<ProfileSummary>(`/profiles/${profileId}/start-training?acknowledge_warnings=${acknowledgeWarnings}`),
    onSuccess: (_data, { profileId }) => {
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
      queryClient.invalidateQueries({ queryKey: ["profiles", profileId] });
      queryClient.invalidateQueries({
        queryKey: ["profiles", profileId, "training-status"],
      });
    },
  });
}

export function useActivateProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ profileId, acknowledgeWarnings = false }: { profileId: string; acknowledgeWarnings?: boolean }) =>
      api.post<ProfileSummary>(`/profiles/${profileId}/activate?acknowledge_warnings=${acknowledgeWarnings}`),
    onSuccess: (_data, { profileId }) => {
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
      queryClient.invalidateQueries({ queryKey: ["profiles", profileId] });
    },
  });
}

export function useRetireProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (profileId: string) =>
      api.post<ProfileSummary>(`/profiles/${profileId}/retire`),
    onSuccess: (_data, profileId) => {
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
      queryClient.invalidateQueries({ queryKey: ["profiles", profileId] });
    },
  });
}

export function useUploadTrainingInvoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ profileId, file }: { profileId: string; file: File }) =>
      api.upload<TrainingUploadResponse>(`/profiles/${profileId}/training-invoices`, file),
    onSuccess: (_data, { profileId }) => {
      queryClient.invalidateQueries({ queryKey: ["profiles", profileId, "training-status"] });
    },
  });
}

export function useTrainingStatus(profileId: string, enabled = true, pollMs = 5000) {
  return useQuery({
    queryKey: ["profiles", profileId, "training-status"],
    queryFn: () => api.get<TrainingStatus>(`/profiles/${profileId}/training-status`),
    enabled: !!profileId && enabled,
    refetchInterval: pollMs,
  });
}
