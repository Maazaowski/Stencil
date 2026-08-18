"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";

export interface SessionInfo {
  id: number;
  email: string;
  username: string | null;
  role: "admin" | "user" | string;
  is_admin: boolean;
}

/** Current login session (401 = not logged in; the api client redirects). */
export function useSession() {
  return useQuery<SessionInfo>({
    queryKey: ["session"],
    queryFn: () => api.get<SessionInfo>("/auth/me"),
    retry: false,
    staleTime: 60_000,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; password: string }) =>
      api.post<SessionInfo>("/auth/login", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["session"] });
    },
  });
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      api.post<{ status: string }>("/auth/change-password", body),
  });
}

export function useLogout() {
  return useMutation({
    mutationFn: () => api.post<{ status: string }>("/auth/logout"),
    onSettled: () => {
      // Full reload clears all client caches along with the session.
      window.location.assign("/login");
    },
  });
}
