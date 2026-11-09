/**
 * Typed client for the AutoApply AI API. All requests go to the dashboard's own origin
 * (`/api/v1/*`, proxied to FastAPI by next.config.js) so the httpOnly session cookie is sent.
 */

export const API_BASE = "/api/v1";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

function errorMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length) {
      return detail
        .map((d: { msg?: string; loc?: unknown[] }) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`)
        .join("; ");
    }
  }
  return fallback;
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const response = await fetch(path.startsWith("http") ? path : `${API_BASE}${path}`, {
    credentials: "include",
    ...rest,
    headers: {
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json().catch(() => null) : null;
  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth/")) {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.href = `/login?next=${next}`;
    }
    throw new ApiError(response.status, errorMessage(body, `Request failed (${response.status})`), body);
  }
  return body as T;
}

export const fetcher = <T,>(path: string) => api<T>(path);

export const post = <T = unknown,>(path: string, json?: unknown) => api<T>(path, { method: "POST", json: json ?? {} });
export const put = <T = unknown,>(path: string, json: unknown) => api<T>(path, { method: "PUT", json });
export const patch = <T = unknown,>(path: string, json: unknown) => api<T>(path, { method: "PATCH", json });
export const del = <T = unknown,>(path: string, json?: unknown) => api<T>(path, { method: "DELETE", json });

export async function upload<T = unknown>(path: string, form: FormData): Promise<T> {
  return api<T>(path, { method: "POST", body: form });
}
