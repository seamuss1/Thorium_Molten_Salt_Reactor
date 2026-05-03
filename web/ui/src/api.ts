import type { AuthSession, CaseDetail, CaseSummary, DocRecord, DocSummary, RateLimitRecord, RunRecord, SimulationDraft } from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail ?? detail;
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
