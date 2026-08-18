import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  PaginatedResponse,
  ProcessingLog,
  ExtractionJob,
  CostDataPoint,
} from "@/types";

export function usePipelineLogs(params?: {
  page?: number;
  per_page?: number;
  intake_id?: string;
  step?: string;
  status?: string;
}) {
  return useQuery({
    queryKey: ["pipeline-logs", params],
    queryFn: () =>
      api.get<PaginatedResponse<ProcessingLog>>("/pipeline/logs", {
        page: params?.page ?? 1,
        per_page: params?.per_page ?? 25,
        intake_id: params?.intake_id,
        step: params?.step,
        status: params?.status,
      }),
  });
}

export function usePipelineJobs(params?: {
  page?: number;
  per_page?: number;
  status?: string;
  extraction_path?: string;
}) {
  return useQuery({
    queryKey: ["pipeline-jobs", params],
    queryFn: () =>
      api.get<PaginatedResponse<ExtractionJob>>("/pipeline/jobs", {
        page: params?.page ?? 1,
        per_page: params?.per_page ?? 25,
        status: params?.status,
        extraction_path: params?.extraction_path,
      }),
  });
}

export function useCostTrends(days: number = 30) {
  return useQuery({
    queryKey: ["dashboard-costs", days],
    queryFn: () => api.get<CostDataPoint[]>("/dashboard/costs", { days }),
  });
}
