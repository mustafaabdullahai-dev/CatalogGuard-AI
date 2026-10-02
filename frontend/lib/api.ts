/**
 * Typed client for the backend.
 *
 * Server components call this directly rather than going through the browser,
 * so the dashboard needs no CORS grant and the session cookie stays server-side.
 *
 * `safe*` helpers return fixtures instead of throwing: this shell has to render
 * before the backend is reachable, and an empty table with an honest banner is
 * more useful than a stack trace.
 */

import { fixtures } from "./fixtures";
import type {
  AgentRun,
  ApprovalRequest,
  DashboardSummary,
  HealthScore,
  Product,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const WORKSPACE = process.env.CATALOGGUARD_WORKSPACE_ID ?? "";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}/api/v1${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(WORKSPACE ? { "x-workspace-id": WORKSPACE } : {}),
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new ApiError(response.status, `${init?.method ?? "GET"} ${path} failed`);
  }
  return (await response.json()) as T;
}

export function usingFixtures(): boolean {
  return !WORKSPACE;
}

export async function getDashboard(): Promise<DashboardSummary> {
  if (usingFixtures()) return fixtures.dashboard;
  return request<DashboardSummary>("/dashboard");
}

export async function getProducts(): Promise<Product[]> {
  if (usingFixtures()) return fixtures.products;
  return request<Product[]>("/products");
}

export async function getApprovals(): Promise<ApprovalRequest[]> {
  if (usingFixtures()) return fixtures.approvals;
  return request<ApprovalRequest[]>("/approvals");
}

export async function getHealthScore(): Promise<HealthScore> {
  if (usingFixtures()) return fixtures.health;
  return request<HealthScore>("/health-score");
}

export async function getAgentRuns(): Promise<AgentRun[]> {
  if (usingFixtures()) return fixtures.agentRuns;
  return request<AgentRun[]>("/agent-runs");
}
