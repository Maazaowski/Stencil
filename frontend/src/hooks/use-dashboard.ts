import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  DashboardStats,
  VolumeDataPoint,
  CostDataPoint,
  MonthlyStats,
  MonthlyCostPoint,
  CostByModelPoint,
  CostBySupplierPoint,
} from "@/types";
import { useHasActiveProcessing } from "@/hooks/use-invoices";

export function useDashboardStats() {
  const { data: hasActiveProcessing } = useHasActiveProcessing();
  return useQuery({
    queryKey: ["dashboard", "stats"],
    queryFn: () => api.get<DashboardStats>("/dashboard/stats"),
    refetchInterval: hasActiveProcessing ? 5000 : false,
  });
}

export function useVolumeData(days?: number) {
  const { data: hasActiveProcessing } = useHasActiveProcessing();
  return useQuery({
    queryKey: ["dashboard", "volume", days],
    queryFn: () =>
      api.get<VolumeDataPoint[]>("/dashboard/volume", { days: days ?? 30 }),
    refetchInterval: hasActiveProcessing ? 5000 : false,
  });
}

export function useCostData(days?: number) {
  const { data: hasActiveProcessing } = useHasActiveProcessing();
  return useQuery({
    queryKey: ["dashboard", "costs", days],
    queryFn: () =>
      api.get<CostDataPoint[]>("/dashboard/costs", { days: days ?? 30 }),
    refetchInterval: hasActiveProcessing ? 5000 : false,
  });
}

export function useMonthlyStats(month: string) {
  const { data: hasActiveProcessing } = useHasActiveProcessing();
  return useQuery({
    queryKey: ["dashboard", "monthly", month],
    queryFn: () => api.get<MonthlyStats>("/dashboard/monthly", { month }),
    refetchInterval: hasActiveProcessing ? 5000 : false,
  });
}

export function useCostByMonth(months?: number) {
  const { data: hasActiveProcessing } = useHasActiveProcessing();
  return useQuery({
    queryKey: ["dashboard", "cost-by-month", months],
    queryFn: () =>
      api.get<MonthlyCostPoint[]>("/dashboard/cost-by-month", {
        months: months ?? 12,
      }),
    refetchInterval: hasActiveProcessing ? 5000 : false,
  });
}

export function useCostByModel(month: string) {
  const { data: hasActiveProcessing } = useHasActiveProcessing();
  return useQuery({
    queryKey: ["dashboard", "cost-by-model", month],
    queryFn: () =>
      api.get<CostByModelPoint[]>("/dashboard/cost-by-model", { month }),
    refetchInterval: hasActiveProcessing ? 5000 : false,
  });
}

export function useCostBySupplier(month: string) {
  const { data: hasActiveProcessing } = useHasActiveProcessing();
  return useQuery({
    queryKey: ["dashboard", "cost-by-supplier", month],
    queryFn: () =>
      api.get<CostBySupplierPoint[]>("/dashboard/cost-by-supplier", { month }),
    refetchInterval: hasActiveProcessing ? 5000 : false,
  });
}
