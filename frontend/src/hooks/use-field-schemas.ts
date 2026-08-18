import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { FieldDef, FieldSchema, FieldSchemaSummary } from "@/types";

interface FieldSchemaListResponse {
  schemas: FieldSchemaSummary[];
}

export function useFieldSchemas() {
  return useQuery({
    queryKey: ["field-schemas"],
    queryFn: async () => {
      const data = await api.get<FieldSchemaListResponse>("/field-schemas");
      return data.schemas;
    },
  });
}

export function useFieldSchema(schemaId: string) {
  return useQuery({
    queryKey: ["field-schemas", schemaId],
    queryFn: () => api.get<FieldSchema>(`/field-schemas/${schemaId}`),
    enabled: !!schemaId && schemaId !== "new",
  });
}

export function useCreateFieldSchema() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      schema_id: string;
      name?: string;
      template?: string | null;
      fields?: FieldDef[];
    }) => api.post<FieldSchemaSummary>("/field-schemas", payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["field-schemas"] });
    },
  });
}

export function useUpdateFieldSchema() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ schemaId, schema }: { schemaId: string; schema: FieldSchema }) =>
      api.put<FieldSchema>(`/field-schemas/${schemaId}`, schema),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["field-schemas"] });
      queryClient.invalidateQueries({ queryKey: ["field-schemas", variables.schemaId] });
    },
  });
}

export function useDeleteFieldSchema() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (schemaId: string) => api.delete(`/field-schemas/${schemaId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["field-schemas"] });
    },
  });
}

export function useCloneFieldSchema() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      schemaId,
      newSchemaId,
      name,
    }: {
      schemaId: string;
      newSchemaId: string;
      name?: string;
    }) =>
      api.post<FieldSchema>(`/field-schemas/${schemaId}/clone`, {
        new_schema_id: newSchemaId,
        name,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["field-schemas"] });
    },
  });
}
