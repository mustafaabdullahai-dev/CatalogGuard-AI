/** Shapes mirrored from the backend models. Kept hand-written on purpose: a
 *  generated client would hide drift until a page rendered `undefined`. */

export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export type ConflictSeverity = "error" | "warning" | "info";

export interface HealthScore {
  overall: number;
  data_quality: number;
  consistency: number;
  compliance: number;
  relationship_integrity: number;
  seo_readiness: number;
  change_safety: number;
  computed_at: string;
}

export interface Product {
  id: string;
  sku: string;
  title: string | null;
  brand: string | null;
  category_path: string | null;
  status: "active" | "draft" | "archived" | "suppressed";
  is_parent: boolean;
  child_count: number;
  open_conflicts: number;
  channel_count: number;
  price: number | null;
  currency: string;
}

export interface CompetingValue {
  source: string;
  value: string;
  rank: number;
}

export interface ApprovalRequest {
  id: string;
  change_ref: string;
  product_id: string | null;
  product_title: string | null;
  field_name: string;
  current_value: string | null;
  proposed_value: string | null;
  risk_level: RiskLevel;
  risk_score: number;
  risk_factors: { code: string; weight: number; detail: string }[];
  confidence: number;
  status: "pending" | "approved" | "rejected" | "modified";
  required_approvals: number;
  approvals_received: number;
  requested_at: string;
  channels: string[];
  competing_values: CompetingValue[];
  reason: string;
}

export type AgentState = "running" | "waiting" | "done" | "failed";

export interface AgentRun {
  id: string;
  node: string;
  state: AgentState;
  detail: string;
  started_at: string;
  duration_ms: number | null;
}

export interface DashboardSummary {
  workspace_name: string;
  products_total: number;
  products_changed_24h: number;
  conflicts_open: number;
  conflicts_tied: number;
  proposals_pending: number;
  executions_today: number;
  executions_failed: number;
}
