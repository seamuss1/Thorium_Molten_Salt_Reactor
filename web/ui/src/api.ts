import type {
  AuthSession,
  CaseDetail,
  CaseSummary,
  DocRecord,
  DocSummary,
  DraftValidationResponse,
  RateLimitRecord,
  RunRecord,
  SimulationDraft
} from "./types";

/**
 * Render an error body as a sentence a person can act on.
 *
 * FastAPI answers a schema violation with `detail` as an array of objects, so
 * assigning it straight to an Error message rendered as "[object Object]" and
 * told the user nothing about which field was wrong.
 */
function formatErrorDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object") {
        const entry = item as { loc?: unknown[]; msg?: string };
        // Drop the leading "body" segment; the field path is what matters.
        const field = Array.isArray(entry.loc)
          ? entry.loc.filter((part) => part !== "body").join(".")
          : "";
        const message = entry.msg ?? "is invalid";
        return field ? `${field}: ${message}` : message;
      }
      return String(item);
    });
    return messages.length ? messages.join("; ") : null;
  }
  if (detail && typeof detail === "object") {
    const entry = detail as { msg?: string; message?: string };
    return entry.msg ?? entry.message ?? JSON.stringify(detail);
  }
  return null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = formatErrorDetail(payload.detail) ?? detail;
    } catch {
      // Keep the HTTP status text.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  me: () => request<AuthSession>("/api/me"),
  cases: () => request<CaseSummary[]>("/api/cases"),
  caseDetail: (caseName: string) => request<CaseDetail>(`/api/cases/${caseName}`),
  runs: () => request<RunRecord[]>("/api/runs"),
  run: (caseName: string, runId: string) => request<RunRecord>(`/api/runs/${caseName}/${runId}`),
  createRun: (draft: SimulationDraft) =>
    request<RunRecord>("/api/runs", { method: "POST", body: JSON.stringify(draft) }),
  validateDraft: (caseName: string, patch: Record<string, unknown>) =>
    request<DraftValidationResponse>(`/api/cases/${caseName}/validate-draft`, {
      method: "POST",
      body: JSON.stringify({ patch })
    }),
  rateLimits: () => request<RateLimitRecord[]>("/api/admin/rate-limits"),
  resetRateLimit: (email: string) =>
    request<RateLimitRecord>(`/api/admin/rate-limits/${encodeURIComponent(email)}/reset`, { method: "POST" }),
  docs: () => request<DocSummary[]>("/api/docs"),
  doc: (slug: string) => request<DocRecord>(`/api/docs/${slug}`)
};

export async function fetchText(url: string): Promise<string> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(response.statusText);
  }
  return response.text();
}
