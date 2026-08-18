import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { ExceptionRecord, MessageResponse } from "@/types";

export function useExceptions() {
  return useQuery({
    queryKey: ["exceptions"],
    queryFn: () => api.get<ExceptionRecord[]>("/exceptions"),
  });
}

export function useException(intakeId: string) {
  return useQuery({
    queryKey: ["exceptions", intakeId],
    queryFn: () => api.get<ExceptionRecord>(`/exceptions/${intakeId}`),
    enabled: !!intakeId,
  });
}

export function useRetryException() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (intakeId: string) =>
      api.post<MessageResponse>(`/exceptions/${intakeId}/retry`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["exceptions"] });
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
  });
}

export function useResolveException() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ intakeId, notes }: { intakeId: string; notes?: string }) =>
      api.put<MessageResponse>(`/exceptions/${intakeId}/resolve`, { notes }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["exceptions"] });
    },
  });
}
