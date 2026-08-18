/**
 * Base API client for communicating with the FastAPI backend.
 * In development, Next.js rewrites /api/v1/* to http://localhost:8000/api/v1/*
 */

const BASE_URL = "/api/v1";

export class ApiError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    public body?: unknown
  ) {
    super(`API Error ${status}: ${statusText}`);
    this.name = "ApiError";
  }
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    // Session expired or not logged in: send the user to /login. Two carve-outs:
    // the login page handles its own 401s, and the /auth/me probe must NOT
    // redirect — it 401s harmlessly in open mode (no users bootstrapped yet),
    // where every real endpoint still works.
    if (
      response.status === 401 &&
      window.location.pathname !== "/login" &&
      !response.url.endsWith("/auth/me")
    ) {
      window.location.assign("/login");
    }
    // A Response body can only be read ONCE — read it as text, then try to
    // parse JSON from that text (reading .json() then .text() throws
    // "body stream already read" and masks the real error).
    const raw = await response.text();
    let body: unknown = raw;
    try {
      body = raw ? JSON.parse(raw) : undefined;
    } catch {
      // not JSON — keep the raw text body
    }
    throw new ApiError(response.status, response.statusText, body);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export const api = {
  async get<T>(path: string, params?: Record<string, string | number | boolean | undefined>): Promise<T> {
    const url = new URL(`${BASE_URL}${path}`, window.location.origin);
    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== "") {
          url.searchParams.set(key, String(value));
        }
      });
    }
    const response = await fetch(url.toString(), {
      headers: { "Content-Type": "application/json" },
    });
    return handleResponse<T>(response);
  },

  async post<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${BASE_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    return handleResponse<T>(response);
  },

  async put<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${BASE_URL}${path}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    return handleResponse<T>(response);
  },

  async patch<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${BASE_URL}${path}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    return handleResponse<T>(response);
  },

  async delete<T>(path: string): Promise<T> {
    const response = await fetch(`${BASE_URL}${path}`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
    });
    return handleResponse<T>(response);
  },

  async upload<T>(path: string, file: File, fields?: Record<string, string>): Promise<T> {
    const formData = new FormData();
    formData.append("file", file);
    if (fields) {
      Object.entries(fields).forEach(([key, value]) => formData.append(key, value));
    }
    const response = await fetch(`${BASE_URL}${path}`, {
      method: "POST",
      body: formData,
    });
    return handleResponse<T>(response);
  },
};
