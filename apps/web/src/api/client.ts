/**
 * Minimal typed API client for the refactored backend.
 *
 * - Base URL comes from `VITE_API_URL` (empty = same-origin `/api`,
 *   which the Vite dev proxy and prod nginx forward to the backend).
 * - Every workspace-scoped route now requires
 *   `Authorization: Bearer <guest-token>` (FR-01), so all helpers take
 *   the token issued by `POST /sessions`.
 */

const API_BASE: string =
  (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') ?? '';

export function apiUrl(path: string): string {
  const normalized = path.startsWith('/') ? path : `/${path}`;
  if (normalized.startsWith('/api/')) return `${API_BASE}${normalized}`;
  return `${API_BASE}/api${normalized}`;
}

export function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string };
}

export async function apiFetch<T>(
  path: string,
  token: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
      ...authHeaders(token),
    },
  });
  if (!response.ok) {
    let code = 'upstream_error';
    let message = `Request failed with status ${response.status}`;
    try {
      const body = (await response.json()) as ErrorEnvelope;
      if (body.error?.code) code = body.error.code;
      if (body.error?.message) message = body.error.message;
    } catch {
      // Non-JSON error body — keep the default message.
    }
    throw new ApiError(response.status, code, message);
  }
  return (await response.json()) as T;
}
