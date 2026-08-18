import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";

export interface AppUser {
  id: number;
  email: string;
  username: string | null;
  role: "admin" | "user" | string;
  is_active: boolean;
  created_at: string;
  updated_at: string | null;
  deleted_at: string | null;
  last_login_at: string | null;
}

export interface CreateUserInput {
  email: string;
  username?: string | null;
  password: string;
  role: "admin" | "user";
}

export interface UpdateUserInput {
  email?: string;
  username?: string | null;
  password?: string;
  role?: "admin" | "user";
  is_active?: boolean;
}

export function useUsers(enabled = true) {
  return useQuery({
    queryKey: ["users"],
    queryFn: () => api.get<AppUser[]>("/users"),
    enabled,
  });
}

export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateUserInput) => api.post<AppUser>("/users", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] });
    },
  });
}

export function useUpdateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: UpdateUserInput }) =>
      api.patch<AppUser>(`/users/${id}`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] });
    },
  });
}

export function useDeleteUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<AppUser>(`/users/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] });
    },
  });
}
