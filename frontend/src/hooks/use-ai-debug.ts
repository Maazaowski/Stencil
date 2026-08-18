"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { AiDebugCall } from "@/types";

/** List recent captured AI calls (dev-only; 403 when debug is off). */
export function useAiCalls() {
  return useQuery({
    queryKey: ["ai-calls"],
    queryFn: () => api.get<{ calls: AiDebugCall[] }>("/debug/ai-calls"),
    refetchInterval: 5000,
  });
}

/** Full Markdown dump (prompt + schema + response) for one captured call. */
export function useAiCall(name: string | null) {
  return useQuery({
    queryKey: ["ai-call", name],
    queryFn: () => api.get<{ name: string; markdown: string }>(`/debug/ai-calls/${encodeURIComponent(name!)}`),
    enabled: !!name,
  });
}

export function useClearAiCalls() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<{ removed: number }>("/debug/ai-calls"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["ai-calls"] }),
  });
}
