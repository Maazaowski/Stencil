"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  EvalBaseline,
  EvalCaseDetail,
  EvalCaseRef,
  EvalCompareResult,
  EvalRunDetail,
  EvalRunStatus,
  EvalRunSummary,
} from "@/types";

/** Available labeled cases + call types (dev-only; 403 when debug is off). */
export function useEvalCases() {
  return useQuery({
    queryKey: ["eval-cases"],
    queryFn: () => api.get<{ call_types: string[]; cases: EvalCaseRef[] }>("/debug/evals/cases"),
  });
}

export function useEvalRuns() {
  return useQuery({
    queryKey: ["eval-runs"],
    queryFn: () => api.get<{ runs: EvalRunSummary[] }>("/debug/evals/runs"),
    refetchInterval: 2000,
  });
}

/** A run's detail + live status; polls while the run is still executing. */
export function useEvalRun(runId: string | null) {
  return useQuery({
    queryKey: ["eval-run", runId],
    queryFn: () => api.get<{ run: EvalRunDetail | null; status: EvalRunStatus | null }>(`/debug/evals/runs/${runId}`),
    enabled: !!runId,
    refetchInterval: (query) => {
      const s = query.state.data?.status?.status;
      return s === "queued" || s === "running" || s === "cancelling" ? 2000 : false;
    },
  });
}

export function useEvalCase(runId: string | null, caseFile: string | null) {
  return useQuery({
    queryKey: ["eval-case", runId, caseFile],
    queryFn: () => api.get<EvalCaseDetail>(`/debug/evals/runs/${runId}/cases/${encodeURIComponent(caseFile!)}`),
    enabled: !!runId && !!caseFile,
  });
}

export function useStartEvalRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      call_types: string[];
      label: string;
      concurrency?: number;
      case_ids?: string[];
      run_kind?: "baseline" | "current";
    }) =>
      api.post<{ run_id: string; status: string }>("/debug/evals/runs", body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["eval-runs"] }),
  });
}

export function useEvalBaseline() {
  return useQuery({
    queryKey: ["eval-baseline"],
    queryFn: () => api.get<{ baseline: EvalBaseline | null }>("/debug/evals/baseline"),
  });
}

export function useSetEvalBaseline() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      api.put<{ baseline: EvalBaseline }>("/debug/evals/baseline", { run_id: runId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["eval-baseline"] });
      queryClient.invalidateQueries({ queryKey: ["eval-runs"] });
    },
  });
}

export function useCompareEvalRuns(a: string | null, b: string | null) {
  return useQuery({
    queryKey: ["eval-compare", a, b],
    queryFn: () => api.get<EvalCompareResult>(`/debug/evals/compare?a=${a}&b=${b}`),
    enabled: !!a && !!b,
  });
}

export function useCancelEvalRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      api.post<{ status: string }>(`/debug/evals/runs/${runId}/cancel`),
    onSuccess: (_d, runId) => {
      queryClient.invalidateQueries({ queryKey: ["eval-run", runId] });
      queryClient.invalidateQueries({ queryKey: ["eval-runs"] });
    },
  });
}
