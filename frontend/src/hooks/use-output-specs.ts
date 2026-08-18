import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { OutputSpec, OutputSpecSummary } from "@/types";

interface OutputSpecListResponse {
  specs: OutputSpecSummary[];
}

export function useOutputSpecs() {
  return useQuery({
    queryKey: ["output-specs"],
    queryFn: async () => {
      const data = await api.get<OutputSpecListResponse>("/output-specs");
      return data.specs;
    },
  });
}

export function useOutputSpec(specId: string) {
  return useQuery({
    queryKey: ["output-specs", specId],
    queryFn: () => api.get<OutputSpec>(`/output-specs/${specId}`),
    enabled: !!specId && specId !== "new",
  });
}

export function useCreateOutputSpec() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (spec: OutputSpec) => api.post<OutputSpecSummary>("/output-specs", spec),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["output-specs"] });
    },
  });
}

export function useUpdateOutputSpec() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ specId, spec }: { specId: string; spec: OutputSpec }) =>
      api.put<OutputSpec>(`/output-specs/${specId}`, spec),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["output-specs"] });
      queryClient.invalidateQueries({ queryKey: ["output-specs", variables.specId] });
    },
  });
}

export function useDeleteOutputSpec() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (specId: string) => api.delete(`/output-specs/${specId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["output-specs"] });
    },
  });
}

export function useCloneOutputSpec() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      specId,
      newSpecId,
      name,
    }: {
      specId: string;
      newSpecId: string;
      name?: string;
    }) =>
      api.post<OutputSpec>(`/output-specs/${specId}/clone`, {
        new_spec_id: newSpecId,
        name,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["output-specs"] });
    },
  });
}
