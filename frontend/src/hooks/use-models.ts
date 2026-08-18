import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import { modelApiPath } from "@/lib/model-routes";
import type {
  PaginatedResponse,
  ExtractionModel,
  ExtractionPreview,
  MessageResponse,
} from "@/types";

/** Run a model against an uploaded sample PDF and return its deliverable preview. */
export function useModelPreview(modelId: string) {
  return useMutation({
    mutationFn: (file: File) =>
      api.upload<ExtractionPreview>(modelApiPath(modelId, "/preview"), file),
  });
}

export function useModels(params?: {
  page?: number;
  per_page?: number;
  status?: string;
  supplier?: string;
}) {
  return useQuery({
    queryKey: ["models", params],
    queryFn: () =>
      api.get<PaginatedResponse<ExtractionModel>>("/models", {
        page: params?.page ?? 1,
        per_page: params?.per_page ?? 20,
        status: params?.status,
        supplier: params?.supplier,
      }),
  });
}

export function useModel(modelId: string) {
  return useQuery({
    queryKey: ["models", modelId],
    queryFn: () => api.get<ExtractionModel>(modelApiPath(modelId)),
    enabled: !!modelId,
  });
}

export function useBootstrapModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (supplierProfileId: string) =>
      api.post<ExtractionModel>("/models/bootstrap", {
        supplier_profile_id: supplierProfileId,
      }),
    onSuccess: (model) => {
      queryClient.invalidateQueries({ queryKey: ["models"] });
      queryClient.invalidateQueries({ queryKey: ["models", model.id] });
    },
  });
}

export interface ModelTrainingRun {
  id: string;
  model_id: string;
  state: "running" | "success" | "failed";
  step: string | null;
  message: string | null;
  completed_steps: number;
  total_steps: number;
  detail: Record<string, unknown>;
  estimated_cost_usd: number;
  tokens_input: number;
  tokens_output: number;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface TrainingStatus {
  running: boolean;
  run: ModelTrainingRun | null;
}

export function useModelTrainingStatus(modelId: string, enabled = true, forcePoll = false) {
  return useQuery({
    queryKey: ["models", modelId, "training-status"],
    queryFn: () => api.get<TrainingStatus>(modelApiPath(modelId, "/training-status")),
    enabled: enabled && !!modelId,
    // Poll while a run is active OR just-triggered (the worker may not have
    // created the run row yet). Poll-based by design — no WebSocket.
    refetchInterval: (query) => (query.state.data?.running || forcePoll ? 1500 : false),
  });
}

export function useApproveModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ modelId, approvedBy }: { modelId: string; approvedBy?: string }) =>
      api.put<ExtractionModel>(modelApiPath(modelId, "/approve"), {
        approved_by: approvedBy ?? null,
      }),
    onSuccess: (model) => {
      queryClient.invalidateQueries({ queryKey: ["models"] });
      queryClient.invalidateQueries({ queryKey: ["models", model.id] });
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
      if (model.supplier_profile_id) {
        queryClient.invalidateQueries({
          queryKey: ["profiles", model.supplier_profile_id],
        });
        queryClient.invalidateQueries({
          queryKey: ["profiles", model.supplier_profile_id, "training-status"],
        });
      }
    },
  });
}

export function useRetireModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (modelId: string) =>
      api.put<ExtractionModel>(modelApiPath(modelId, "/retire")),
    onSuccess: (model) => {
      queryClient.invalidateQueries({ queryKey: ["models"] });
      queryClient.invalidateQueries({ queryKey: ["models", model.id] });
    },
  });
}

export function useDeleteModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (modelId: string) =>
      api.delete<MessageResponse>(modelApiPath(modelId)),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["models"] });
    },
  });
}

export function useUpdateModelRules() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ modelId, modelJson }: { modelId: string; modelJson: Record<string, unknown> }) =>
      api.put<ExtractionModel>(modelApiPath(modelId, "/rules"), { model_json: modelJson }),
    onSuccess: (model) => {
      queryClient.invalidateQueries({ queryKey: ["models"] });
      queryClient.invalidateQueries({ queryKey: ["models", model.id] });
    },
  });
}

export function useActivateModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (modelId: string) =>
      api.post<ExtractionModel>(modelApiPath(modelId, "/activate")),
    onSuccess: (model) => {
      queryClient.invalidateQueries({ queryKey: ["models"] });
      queryClient.invalidateQueries({ queryKey: ["models", model.id] });
    },
  });
}

export function useCloneModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (modelId: string) =>
      api.post<ExtractionModel>(modelApiPath(modelId, "/clone")),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["models"] });
    },
  });
}

export interface TrainingSetInvoice {
  intake_id: string;
  filename: string | null;
  line_count: number | null;
  pages: number | null;
  size_bytes: number | null;
  ok: boolean;
  reason: string;
  staged: boolean;
  trained: boolean;
}

export interface TrainingSetLastRun {
  id: string;
  state: "running" | "success" | "failed";
  attempts: number | null;
  finished_at: string | null;
}

export interface TrainingSet {
  training_intake_ids: string[];
  holdout_intake_ids: string[];
  required_count: number;
  reproduces_all: boolean;
  holdout_reproduces_all: boolean;
  approvable: boolean;
  reasons: string[];
  warnings: string[];
  invoices: TrainingSetInvoice[];
  holdout: TrainingSetInvoice[];
  last_run: TrainingSetLastRun | null;
}

export function useTrainingSet(modelId: string, options?: { pollMs?: number }) {
  return useQuery({
    queryKey: ["models", modelId, "training-set"],
    queryFn: () => api.get<TrainingSet>(modelApiPath(modelId, "/training-set")),
    enabled: !!modelId,
    refetchInterval: options?.pollMs ?? false,
  });
}

export interface ModelTrainingUploadResult {
  intake_id: string;
  model_id: string;
  role: "train" | "holdout";
  status: string;
  cloned: boolean;
}

export function useUploadTrainingInvoiceToModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      modelId,
      file,
      role,
    }: {
      modelId: string;
      file: File;
      role: "train" | "holdout";
    }) =>
      api.upload<ModelTrainingUploadResult>(
        modelApiPath(modelId, "/training-invoices"),
        file,
        { role },
      ),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["models"] });
      queryClient.invalidateQueries({ queryKey: ["models", res.model_id] });
      queryClient.invalidateQueries({ queryKey: ["models", res.model_id, "training-set"] });
      queryClient.invalidateQueries({ queryKey: ["models", res.model_id, "versions"] });
    },
  });
}

export function useUpdateTrainingSet() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      modelId,
      trainIntakeIds,
      holdoutIntakeIds,
    }: {
      modelId: string;
      trainIntakeIds: string[];
      holdoutIntakeIds: string[];
    }) =>
      api.put<ExtractionModel>(modelApiPath(modelId, "/training-set"), {
        train_intake_ids: trainIntakeIds,
        holdout_intake_ids: holdoutIntakeIds,
      }),
    // Instant Train <-> Holdout transfer: patch the cached training-set
    // immediately, roll back on error, reconcile on settle. No blocking refetch.
    onMutate: async ({ modelId, trainIntakeIds, holdoutIntakeIds }) => {
      const key = ["models", modelId, "training-set"];
      await queryClient.cancelQueries({ queryKey: key });
      const prev = queryClient.getQueryData<TrainingSet>(key);
      if (prev) {
        const byId = new Map<string, TrainingSetInvoice>();
        [...prev.invoices, ...prev.holdout].forEach((r) => byId.set(r.intake_id, r));
        const rows = (ids: string[]): TrainingSetInvoice[] =>
          ids.map(
            (id) =>
              byId.get(id) ?? {
                intake_id: id,
                filename: null,
                line_count: null,
                pages: null,
                size_bytes: null,
                ok: false,
                reason: "Not trained yet — click Train",
                staged: true,
                trained: false,
              },
          );
        queryClient.setQueryData<TrainingSet>(key, {
          ...prev,
          training_intake_ids: trainIntakeIds,
          holdout_intake_ids: holdoutIntakeIds,
          invoices: rows(trainIntakeIds),
          holdout: rows(holdoutIntakeIds),
        });
      }
      return { prev, key };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(ctx.key, ctx.prev);
    },
    onSettled: (model, _err, { modelId }) => {
      queryClient.invalidateQueries({ queryKey: ["models", modelId, "training-set"] });
      queryClient.invalidateQueries({ queryKey: ["models", modelId] });
      if (model && model.id !== modelId) {
        queryClient.invalidateQueries({ queryKey: ["models", model.id] });
        queryClient.invalidateQueries({ queryKey: ["models", model.id, "training-set"] });
      }
    },
  });
}

export interface ModelVersion {
  model_id: string;
  version: number;
  status: ExtractionModel["status"];
  is_live: boolean;
  confidence: number;
  times_used: number;
  validation_success_count: number;
  validation_attempt_count: number;
  training_invoice_count: number;
  holdout_invoice_count: number;
  created_at: string | null;
  approved_at: string | null;
  approved_by: string | null;
  updated_by?: string | null;
  retired_by?: string | null;
  retired_at?: string | null;
  notes: string | null;
}

export interface ModelVersions {
  profile_id: string | null;
  live_model_id: string | null;
  versions: ModelVersion[];
}

export function useModelVersions(modelId: string) {
  return useQuery({
    queryKey: ["models", modelId, "versions"],
    queryFn: () => api.get<ModelVersions>(modelApiPath(modelId, "/versions")),
    enabled: !!modelId,
  });
}

export function useRollbackModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ modelId, approvedBy }: { modelId: string; approvedBy?: string }) =>
      api.post<ExtractionModel>(modelApiPath(modelId, "/rollback"), {
        approved_by: approvedBy ?? "rollback",
      }),
    onSuccess: (model) => {
      queryClient.invalidateQueries({ queryKey: ["models"] });
      queryClient.invalidateQueries({ queryKey: ["models", model.id] });
      queryClient.invalidateQueries({ queryKey: ["models", model.id, "versions"] });
      queryClient.invalidateQueries({ queryKey: ["profiles"] });
    },
  });
}

export function useTrainModel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ modelId, addIntakeIds }: { modelId: string; addIntakeIds: string[] }) =>
      api.post<MessageResponse>(modelApiPath(modelId, "/train"), {
        add_intake_ids: addIntakeIds,
      }),
    onSuccess: (_res, { modelId }) => {
      queryClient.invalidateQueries({ queryKey: ["models", modelId] });
      queryClient.invalidateQueries({ queryKey: ["models", modelId, "training-set"] });
    },
  });
}

export interface ModelTestResult {
  is_valid: boolean | null;
  reason: string;
  model_output: Record<string, unknown> | null;
  ai_baseline: Record<string, unknown> | null;
}

export function useTestModel() {
  return useMutation({
    mutationFn: ({
      modelId,
      intakeId,
      modelJson,
    }: {
      modelId: string;
      intakeId: string;
      modelJson?: Record<string, unknown>;
    }) =>
      api.post<ModelTestResult>(modelApiPath(modelId, "/test"), {
        intake_id: intakeId,
        model_json: modelJson,
      }),
  });
}
