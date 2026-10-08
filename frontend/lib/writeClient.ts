/**
 * Browser-side client for the writes: approve, reject, modify, request
 * re-analysis, simulate.
 *
 * Server components read; they do not mutate. A mutation needs two things a
 * server component does not have — the CSRF token, and the workspace header for
 * the tenant — so these calls go from the browser through the same-origin
 * `/api/*` rewrite in `next.config.ts`, exactly as `lib/authClient.ts` does for
 * the account flows. Same origin means the session cookie already on this host
 * is the one that travels, and no CORS grant is load-bearing.
 *
 * The workspace id is resolved once per page load from `GET /auth/me` and
 * memoised in module scope. It is not read from a cookie or `localStorage`: the
 * server only accepts an `X-Workspace-Id` that matches a real membership, and
 * asking who you are is the one lookup that cannot be stale.
 */

import type {
  ApprovalDetail,
  BulkDecisionResponse,
  ComplianceSearchResponse,
  DecisionResponse,
  LowRiskResponse,
  SimulationResponse,
} from "./types";

const BASE = "";

export class WriteApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly detail?: string,
  ) {
    super(message);
    this.name = "WriteApiError";
  }
}

let workspaceId: string | null = null;
let workspaceLookup: Promise<string> | null = null;

async function csrfHeader(): Promise<Record<string, string>> {
  const response = await fetch(`${BASE}/api/v1/auth/csrf`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) throw new WriteApiError(response.status, "Could not start a session.");
  const { csrf_token: token, header_name: header } = (await response.json()) as {
    csrf_token: string;
    header_name: string;
  };
  return { [header]: token };
}

export function forgetWorkspace(): void {
  workspaceId = null;
  workspaceLookup = null;
}

export function currentWorkspaceId(): string | null {
  return workspaceId;
}

export async function resolveWorkspaceId(): Promise<string> {
  if (workspaceId) return workspaceId;
  if (!workspaceLookup) {
    workspaceLookup = fetch(`${BASE}/api/v1/auth/me`, { credentials: "include", cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new WriteApiError(response.status, "Not signed in.");
        const body = (await response.json()) as { workspaces: { id: string }[] };
        const first = body.workspaces[0];
        if (!first) throw new WriteApiError(403, "No workspace membership.");
        workspaceId = first.id;
        return first.id;
      })
      .finally(() => {
        workspaceLookup = null;
      });
  }
  return workspaceLookup;
}

async function describe(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    if (typeof body.detail === "string" && body.detail !== "") return body.detail;
  } catch {
    // A non-JSON error body is reported by status instead of swallowed.
  }
  if (response.status === 401) return "Your session has expired. Sign in again.";
  if (response.status === 403) return "You do not have the capability for this action.";
  if (response.status === 404) return "Not found.";
  if (response.status === 409) return "The proposal is no longer in a state that allows this.";
  if (response.status === 422) return "The request was rejected as invalid.";
  return `Request failed (${response.status}).`;
}

async function write<T>(method: "POST" | "DELETE", path: string, body?: unknown): Promise<T> {
  const workspace = await resolveWorkspaceId();
  const header = await csrfHeader();
  const response = await fetch(`${BASE}/api/v1${path}`, {
    method,
    credentials: "include",
    headers: { "content-type": "application/json", "x-workspace-id": workspace, ...header },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    cache: "no-store",
  });
  if (!response.ok) throw new WriteApiError(response.status, await describe(response));
  return (await response.json()) as T;
}

async function readJson<T>(path: string): Promise<T> {
  const workspace = await resolveWorkspaceId();
  const response = await fetch(`${BASE}/api/v1${path}`, {
    credentials: "include",
    headers: { "x-workspace-id": workspace },
    cache: "no-store",
  });
  if (!response.ok) throw new WriteApiError(response.status, await describe(response));
  return (await response.json()) as T;
}

/* --- approval decisions (Phase 18) ----------------------------------- */

export function fetchApprovalDetail(proposalId: string): Promise<ApprovalDetail> {
  return readJson<ApprovalDetail>(`/approvals/${encodeURIComponent(proposalId)}`);
}

export function approve(
  proposalId: string,
  notes?: string,
): Promise<DecisionResponse> {
  return write<DecisionResponse>("POST", `/approvals/${encodeURIComponent(proposalId)}/approve`, {
    notes: notes && notes !== "" ? notes : null,
  });
}

export function reject(proposalId: string, notes: string): Promise<DecisionResponse> {
  return write<DecisionResponse>("POST", `/approvals/${encodeURIComponent(proposalId)}/reject`, { notes });
}

export function modify(proposalId: string, proposedValue: unknown, notes: string): Promise<DecisionResponse> {
  return write<DecisionResponse>("POST", `/approvals/${encodeURIComponent(proposalId)}/modify`, {
    proposed_value: proposedValue,
    notes,
  });
}

export function requestReanalysis(proposalId: string, notes: string): Promise<DecisionResponse> {
  return write<DecisionResponse>("POST", `/approvals/${encodeURIComponent(proposalId)}/re-analysis`, {
    notes,
  });
}

export function decideBulk(
  action: "approve" | "reject",
  proposalIds: readonly string[],
  notes?: string,
): Promise<BulkDecisionResponse> {
  return write<BulkDecisionResponse>("POST", "/approvals", {
    action,
    proposal_ids: [...proposalIds],
    notes: notes && notes !== "" ? notes : null,
  });
}

export function approveAllLowRisk(notes?: string): Promise<LowRiskResponse> {
  const search = notes && notes !== "" ? `?notes=${encodeURIComponent(notes)}` : "";
  return write<LowRiskResponse>("POST", `/approvals/approve-low-risk${search}`);
}

/* --- sandbox (Phase 17) and compliance (Phase 13) -------------------- */

export function simulate(proposalId: string): Promise<SimulationResponse> {
  return write<SimulationResponse>("POST", `/change-proposals/${encodeURIComponent(proposalId)}/simulate`);
}

export function searchCompliance(q: string, topK = 8): Promise<ComplianceSearchResponse> {
  const search = new URLSearchParams({ q, top_k: String(topK) });
  return readJson<ComplianceSearchResponse>(`/compliance/search?${search.toString()}`);
}