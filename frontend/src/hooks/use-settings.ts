"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";

export interface AppSettings {
  // Folder paths (read-only)
  data_dir: string;
  inbound_dir: string;
  processing_dir: string;
  completed_dir: string;
  exceptions_dir: string;
  archive_dir: string;
  supplier_profiles_dir: string;
  extraction_models_dir: string;

  // LLM provider + keys
  llm_provider: string;
  providers: string[];
  provider_model_options: Record<string, string[]>;
  openai_api_key_set: boolean;
  anthropic_api_key_set: boolean;
  secret_storage_enabled: boolean;
  openai_model_classification: string;
  openai_model_extraction: string;
  openai_model_model_generation: string;
  openai_model_options: string[];
  openai_max_retries: number;
  openai_timeout: number;
  openai_max_output_tokens: number;
  ai_extraction_mode: string;
  ai_chunk_max_layout_chars: number;
  ai_chunk_overlap_rows: number;
  ai_chunk_max_retries: number;
  ai_chunk_concurrency: number;
  worker_concurrency: number;

  // Thresholds
  confidence_threshold: number;
  reconciliation_variance_threshold: number;
  model_validation_required_successes: number;
  model_validation_max_failures: number;

  // File watcher
  watcher_poll_interval: number;
  watcher_stable_seconds: number;

  // Misc
  log_level: string;
  debug: boolean;
}

export interface SettingsUpdate {
  llm_provider?: string;
  openai_model_classification?: string;
  openai_model_extraction?: string;
  openai_model_model_generation?: string;
  openai_max_retries?: number;
  openai_timeout?: number;
  openai_max_output_tokens?: number;
  ai_extraction_mode?: string;
  ai_chunk_max_layout_chars?: number;
  ai_chunk_overlap_rows?: number;
  ai_chunk_max_retries?: number;
  ai_chunk_concurrency?: number;
  worker_concurrency?: number;
  confidence_threshold?: number;
  reconciliation_variance_threshold?: number;
  model_validation_required_successes?: number;
  model_validation_max_failures?: number;
  watcher_poll_interval?: number;
  watcher_stable_seconds?: number;
  log_level?: string;
  debug?: boolean;
}

export function useSettings() {
  return useQuery<AppSettings>({
    queryKey: ["settings"],
    queryFn: () => api.get<AppSettings>("/settings"),
  });
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: SettingsUpdate) =>
      api.put<AppSettings>("/settings", payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
  });
}

/** Store a provider's API key (write-only; encrypted server-side, never returned). */
export function useSetProviderKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { provider: string; api_key: string }) =>
      api.put<AppSettings>("/settings/keys", body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });
}

/** Remove a provider's stored key (env-var fallback still applies). */
export function useClearProviderKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (provider: string) =>
      api.delete<AppSettings>(`/settings/keys/${provider}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });
}
