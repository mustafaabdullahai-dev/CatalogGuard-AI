/**
 * Server-side client for the backend.
 *
 * Three things this module has to get right, in order:
 *
 * 1. **The session.** Every tenant route is auth-gated, and the session lives
 *    in a cookie set on *this* origin by the `/api/*` rewrite. A server
 *    component that calls the backend without forwarding the incoming cookies
 *    is calling it anonymously and getting 401, which is why the previous
 *    version of this file could only ever return fixtures.
 * 2. **The workspace.** `X-Workspace-Id` selects the tenant and is validated
 *    against the caller's memberships. It is read from a `cg_workspace` cookie
 *    when the operator has picked one, and otherwise resolved to the first
 *    membership `GET /auth/me` reports.
 * 3. **Honesty about what is on screen.** A screen that silently shows
 *    fixtures where real rows belong is worse than a screen that shows nothing.
 *    Every read returns a `Loaded<T>` carrying the source and, when it is
 *    fixtures, the reason — so the page can say *why* rather than implying the
 *    numbers came from the workspace.
 *
 * The fallback reasons are the same set on every screen: not signed in,
 * backend unreachable, tenant refused, server error, or fixtures forced on.
 */

import { cache } from "react";
import { cookies } from "next/headers";

import * as fixtures from "./fixtures";
import type {
  ApprovalQueuePage,
  CatalogSourcePage,
  ComplianceDocumentPage,
  ComplianceSearchResponse,
  ConflictPage,
  CurrentSession,
  ProductPage,
  ProposalPage,
  ReconciliationRunPage,
  SessionWorkspace,
} from "./types";
import { reasonNotice } from "./notices";

const BASE = process.env.CATALOGGUARD_API_URL ?? "http://127.0.0.1:8000";

/** Written by the workspace switcher; a hint, validated against `/auth/me`. */
export const WORKSPACE_COOKIE = "cg_workspace";

export type FixtureReason = "disabled" | "offline" | "unreachable" | "forbidden" | "server-error";

export interface Loaded<T> {
  data: T;
  source: "live" | "fixture";
  reason: FixtureReason | null;
}

export interface PageContext {
  /** `null` means: no session, so nothing tenant-scoped can be read. */
  workspaces: SessionWorkspace[];
  workspace: SessionWorkspace | null;
  live: boolean;
  reason: FixtureReason | null;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function forcedFixtures(): boolean {
  return process.env.CATALOGGUARD_FIXTURES === "1";
}

async function cookieHeader(): Promise<string> {
  const jar = await cookies();
  return jar.toString();
}

async function raw(path: string, init?: RequestInit & { workspace?: string }): Promise<Response> {
  const { workspace, ...rest } = init ?? {};
  return fetch(`${BASE}/api/v1${path}`, {
    ...rest,
    headers: {
      "content-type": "application/json",
      cookie: await cookieHeader(),
      ...(workspace ? { "x-workspace-id": workspace } : {}),
      ...(rest.headers ?? {}),
    },
    cache: "no-store",
  });
}

/**
 * The session, resolved once per render pass.
 *
 * `cache()` is React's per-request memo, not a global cache: two requests in
 * the same page render share one `/auth/me` call, and the next request asks
 * again. A module-level variable would leak one operator's workspace into
 * another operator's render.
 */
export const currentSession = cache(async (): Promise<CurrentSession | null> => {
  if (forcedFixtures()) return null;
  try {
    const response = await raw("/auth/me");
    if (!response.ok) return null;
    return (await response.json()) as CurrentSession;
  } catch {
    // A backend that is not listening is the normal case while developing.
    return null;
  }
});

async function pickWorkspace(session: CurrentSession | null): Promise<SessionWorkspace | null> {
  if (!session || session.workspaces.length === 0) return null;
  const jar = await cookies();
  const wanted = jar.get(WORKSPACE_COOKIE)?.value;
  if (wanted) {
    const match = session.workspaces.find((workspace) => workspace.id === wanted);
    // A cookie naming a workspace the operator no longer belongs to is stale,
    // not authoritative: fall back rather than 403 the whole console.
    if (match) return match;
  }
  return session.workspaces[0] ?? null;
}

/**
 * What every page needs before it reads anything: who is signed in, which
 * workspace the reads are scoped to, and whether the screens may offer writes.
 */
export async function pageContext(): Promise<PageContext> {
  if (forcedFixtures()) {
    return { workspaces: [], workspace: null, live: false, reason: "disabled" };
  }
  const session = await currentSession();
  if (!session) {
    // Distinguish "no session" from "backend down" without a second request:
    // an empty workspace list is a signed-in user with nothing, which cannot
    // happen (invitations create memberships), so it means no session at all.
    return { workspaces: [], workspace: null, live: false, reason: "offline" };
  }
  const workspace = await pickWorkspace(session);
  if (!workspace) {
    return { workspaces: session.workspaces, workspace: null, live: false, reason: "forbidden" };
  }
  return { workspaces: session.workspaces, workspace, live: true, reason: null };
}

/** Read a tenant-scoped endpoint, falling back to fixtures with a stated reason. */
async function load<T>(
  context: PageContext,
  path: string,
  fallback: T,
  build: (workspaceId: string) => Promise<Response>,
): Promise<Loaded<T>> {
  if (!context.live || !context.workspace) {
    return { data: fallback, source: "fixture", reason: context.reason };
  }
  let response: Response;
  try {
    response = await build(context.workspace.id);
  } catch {
    return { data: fallback, source: "fixture", reason: "unreachable" };
  }
  if (response.status === 401 || response.status === 403) {
    return { data: fallback, source: "fixture", reason: "forbidden" };
  }
  if (!response.ok) {
    return { data: fallback, source: "fixture", reason: "server-error" };
  }
  return { data: (await response.json()) as T, source: "live", reason: null };
}

function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

/* --- reads ------------------------------------------------------------ */

export async function getProducts(
  context: PageContext,
  params: { limit?: number; offset?: number } = {},
): Promise<Loaded<ProductPage>> {
  const limit = params.limit ?? 50;
  const offset = params.offset ?? 0;
  return load(
    context,
    `/products${query({ limit, offset })}`,
    fixtures.products(limit, offset),
    (workspace) => raw(`/products${query({ limit, offset })}`, { workspace }),
  );
}

export async function getConflicts(
  context: PageContext,
  params: { limit?: number; offset?: number; productId?: string; openOnly?: boolean } = {},
): Promise<Loaded<ConflictPage>> {
  const limit = params.limit ?? 50;
  const offset = params.offset ?? 0;
  const path = `/conflicts${query({
    limit,
    offset,
    product_id: params.productId,
    open_only: params.openOnly === false ? "false" : undefined,
  })}`;
  return load(
    context,
    path,
    fixtures.conflicts(limit, offset, params.productId),
    (workspace) => raw(path, { workspace }),
  );
}

export async function getProposals(
  context: PageContext,
  params: { limit?: number; offset?: number; riskLevel?: string } = {},
): Promise<Loaded<ProposalPage>> {
  const limit = params.limit ?? 50;
  const offset = params.offset ?? 0;
  const path = `/change-proposals${query({ limit, offset, risk_level: params.riskLevel })}`;
  return load(
    context,
    path,
    fixtures.proposals(limit, offset, params.riskLevel),
    (workspace) => raw(path, { workspace }),
  );
}

export async function getApprovals(
  context: PageContext,
  params: { limit?: number; offset?: number; riskLevel?: string } = {},
): Promise<Loaded<ApprovalQueuePage>> {
  const limit = params.limit ?? 50;
  const offset = params.offset ?? 0;
  const path = `/approvals${query({ limit, offset, risk_level: params.riskLevel })}`;
  return load(
    context,
    path,
    fixtures.approvals(limit, offset, params.riskLevel),
    (workspace) => raw(path, { workspace }),
  );
}

export async function getSources(context: PageContext): Promise<Loaded<CatalogSourcePage>> {
  return load(context, "/catalog-sources", fixtures.sources(), (workspace) =>
    raw("/catalog-sources", { workspace }),
  );
}

export async function getRuns(context: PageContext): Promise<Loaded<ReconciliationRunPage>> {
  return load(context, "/reconciliation-runs", fixtures.runs(), (workspace) =>
    raw("/reconciliation-runs", { workspace }),
  );
}

export async function getDocuments(context: PageContext): Promise<Loaded<ComplianceDocumentPage>> {
  return load(context, "/compliance/documents", fixtures.documents(), (workspace) =>
    raw("/compliance/documents", { workspace }),
  );
}

export async function searchCompliance(
  context: PageContext,
  params: { q: string; topK?: number },
): Promise<Loaded<ComplianceSearchResponse>> {
  const path = `/compliance/search${query({ q: params.q, top_k: params.topK ?? 8 })}`;
  const fallback = fixtures.search(params.q);
  if (!context.live || !context.workspace) {
    return { data: fallback, source: "fixture", reason: context.reason };
  }
  try {
    const response = await raw(path, { workspace: context.workspace.id });
    if (!response.ok) return { data: fallback, source: "fixture", reason: "server-error" };
    return { data: (await response.json()) as ComplianceSearchResponse, source: "live", reason: null };
  } catch {
    return { data: fallback, source: "fixture", reason: "unreachable" };
  }
}

/**
 * The dashboard has no aggregate route, so its numbers are assembled from the
 * `total` of several real list endpoints fetched with `limit=1`: counting rows
 * on the client would be wrong the moment a workspace has more than a page of
 * them, and the backend already computed these totals with SQL.
 *
 * A `null` means "no route reports this" and the tile says so. A `0` means the
 * workspace genuinely has none, and those two must never look the same.
 */
export interface DashboardCounts {
  productsTotal: number | null;
  openConflicts: number | null;
  pendingApprovals: number | null;
  criticalProposals: number | null;
  highProposals: number | null;
  lastRun: { status: string; productsAnalyzed: number; conflictsFound: number; startedAt: string } | null;
  reasons: FixtureReason[];
}

export async function dashboardCounts(context: PageContext): Promise<DashboardCounts> {
  const empty: DashboardCounts = {
    productsTotal: null,
    openConflicts: null,
    pendingApprovals: null,
    criticalProposals: null,
    highProposals: null,
    lastRun: null,
    reasons: [],
  };
  if (!context.live || !context.workspace) {
    return { ...empty, reasons: context.reason ? [context.reason] : [] };
  }
  const workspace = context.workspace.id;
  const [products, conflicts, approvals, critical, high, runs] = await Promise.all([
    raw(`/products${query({ limit: 1 })}`, { workspace }).catch(() => null),
    raw(`/conflicts${query({ limit: 1, open_only: true })}`, { workspace }).catch(() => null),
    raw(`/approvals${query({ limit: 1 })}`, { workspace }).catch(() => null),
    raw(`/change-proposals${query({ limit: 1, risk_level: "CRITICAL" })}`, { workspace }).catch(() => null),
    raw(`/change-proposals${query({ limit: 1, risk_level: "HIGH" })}`, { workspace }).catch(() => null),
    raw(`/reconciliation-runs${query({ limit: 1 })}`, { workspace }).catch(() => null),
  ]);
  const reasons: FixtureReason[] = [];
  const readTotal = async (response: Response | null): Promise<number | null> => {
    if (!response) {
      reasons.push("unreachable");
      return null;
    }
    if (!response.ok) {
      reasons.push(response.status >= 500 ? "server-error" : "forbidden");
      return null;
    }
    const body = (await response.json()) as { total?: number };
    return typeof body.total === "number" ? body.total : null;
  };
  const runBody = runs && runs.ok ? ((await runs.json()) as ReconciliationRunPage) : null;
  const last = runBody?.runs?.[0] ?? null;
  return {
    productsTotal: await readTotal(products),
    openConflicts: await readTotal(conflicts),
    pendingApprovals: await readTotal(approvals),
    criticalProposals: await readTotal(critical),
    highProposals: await readTotal(high),
    lastRun: last
      ? {
          status: last.status,
          productsAnalyzed: last.products_analyzed,
          conflictsFound: last.conflicts_found,
          startedAt: last.started_at,
        }
      : null,
    reasons: Array.from(new Set(reasons)),
  };
}

/** The one-line explanation a page shows above fixtures. Pure, and tested. */
export function fixtureNotice(source: Loaded<unknown>["source"], reason: FixtureReason | null): string | null {
  return reasonNotice(source, reason);
}