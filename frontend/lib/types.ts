/**
 * Shapes mirrored from the backend response models.
 *
 * Kept hand-written on purpose: a generated client would hide drift until a
 * page rendered `undefined`. Every interface here has a counterpart in
 * `backend/app/schemas/`, and `tests/schemas.test.ts` asserts that the
 * fixtures this app falls back to still satisfy them, so a backend rename
 * breaks a test instead of a screen.
 *
 * Three response shapes are page-shaped rather than row-shaped, and every list
 * endpoint uses one of them: `{ items, total, limit, offset }`. The `total` is
 * what pagination reads; the rows alone cannot tell a reviewer whether they are
 * looking at one page or the whole workspace.
 */

/** The four bands the backend risk engine emits (D-016). */
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/* --- catalog ---------------------------------------------------------- */

export interface ProductRef {
  id: string;
  catalog_id: string;
  sku: string;
  gtin: string | null;
  upc: string | null;
  mpn: string | null;
  brand: string | null;
  manufacturer: string | null;
  title: string | null;
  category_path: string | null;
  status: string;
  version: number;
  is_parent: boolean;
  attributes: Record<string, unknown>;
  last_reconciled_at: string | null;
  created_at: string;
}

export interface ProductPage extends Page<ProductRef> {
  products: ProductRef[];
}

export interface CatalogRef {
  id: string;
  name: string;
  slug: string;
  is_default: boolean;
}

export interface CatalogSourceRef {
  id: string;
  name: string;
  kind: string;
  status: string;
  record_count: number;
  created_at: string;
}

export interface CatalogSourcePage extends Page<CatalogSourceRef> {
  sources: CatalogSourceRef[];
}

/* --- reconciliation ---------------------------------------------------- */

/** `competing_values` is a free-form bag on the wire; §14 keeps it out of prompts. */
export type CompetingValues = Record<string, unknown>;

export interface ConflictRef {
  id: string;
  product_id: string | null;
  conflict_type: string;
  field_name: string;
  competing_values: CompetingValues;
  confidence: number | null;
  reconciliation_run_id: string | null;
  resolved_at: string | null;
  created_at: string;
}

export interface ConflictPage extends Page<ConflictRef> {
  conflicts: ConflictRef[];
}

export interface ChangeProposalRef {
  id: string;
  change_ref: string;
  product_id: string | null;
  field_name: string;
  current_value: unknown;
  proposed_value: unknown;
  reason: string;
  /** Evidence, never permission (§37). */
  confidence: number;
  risk_level: string;
  requires_approval: boolean;
  status: string;
  validation_status: string;
  seo_impact: string;
  compliance_impact: string;
  affected_channels: unknown[];
  created_at: string;
}

export interface ProposalPage extends Page<ChangeProposalRef> {
  proposals: ChangeProposalRef[];
}

export interface SimulationStage {
  name: string;
  status: string;
  detail: string;
}

export interface SimulationResponse {
  simulation_id: string;
  proposal_id: string;
  change_ref: string;
  validation_status: string;
  status: string;
  /** `null` means the stage did not run, which is not `true` (§18). */
  schema_valid: boolean | null;
  relationship_valid: boolean | null;
  compliance_valid: boolean | null;
  seo_sensitive: boolean;
  overall_risk: string;
  base_catalog_version: number;
  stages: SimulationStage[];
  validation_errors: string[];
  diff: Record<string, unknown>;
  impact_report: Record<string, unknown>;
  explanation: string;
}

export interface ReconciliationRunRef {
  id: string;
  catalog_id: string;
  catalog_version: number;
  status: string;
  products_analyzed: number;
  conflicts_found: number;
  started_at: string;
  completed_at: string | null;
}

export interface ReconciliationRunPage extends Page<ReconciliationRunRef> {
  runs: ReconciliationRunRef[];
}

/* --- approvals (Phase 18) -------------------------------------------- */

export interface ApprovalQueueItem {
  proposal: ChangeProposalRef;
  required_approvals: number | null;
  approvals_received: number | null;
  request_status: string | null;
  simulated: boolean;
}

export interface ApprovalQueuePage extends Page<ApprovalQueueItem> {
  items: ApprovalQueueItem[];
}

export interface RiskFactor {
  code: string;
  weight: number;
  detail: string;
}

export interface RiskSummary {
  risk_level: string;
  numeric_score: number;
  factors: RiskFactor[];
  escalated_reason: string | null;
}

export interface SimulationSummary {
  simulation_id: string;
  completed_at: string | null;
  base_catalog_version: number;
  status: string;
  schema_valid: boolean | null;
  relationship_valid: boolean | null;
  compliance_valid: boolean | null;
  seo_sensitive: boolean | null;
  overall_risk: string | null;
  diff: Record<string, unknown>;
  impact_report: Record<string, unknown>;
  explanation: string | null;
}

export interface ApprovalRequestRef {
  id: string;
  status: string;
  required_approvals: number;
  approvals_received: number;
  decided_at: string | null;
}

export interface ApprovalActionRef {
  decision: string;
  decided_by: string;
  notes: string | null;
  modified_value: unknown;
  decided_at: string;
}

export interface ConflictEvidence {
  conflict_id: string;
  conflict_type: string;
  field_name: string;
  competing_values: CompetingValues;
  confidence: number | null;
}

export interface ApprovalDetail {
  proposal: ChangeProposalRef;
  approvable: boolean;
  approvable_reason: string | null;
  risk: RiskSummary | null;
  simulation: SimulationSummary | null;
  request: ApprovalRequestRef | null;
  actions: ApprovalActionRef[];
  conflicts: ConflictEvidence[];
}

export interface DecisionResponse {
  proposal_id: string;
  change_ref: string;
  decision: string;
  proposal_status: string;
  validation_status: string;
  request_status: string;
  approvals_received: number;
  required_approvals: number;
  complete: boolean;
  audit_id: string;
  detail: string;
}

export interface BulkItemResult {
  proposal_id: string;
  change_ref: string | null;
  outcome: string;
  detail: string;
}

export interface BulkDecisionResponse {
  results: BulkItemResult[];
  done: number;
  refused: number;
  considered: number;
}

export interface LowRiskResponse {
  results: BulkItemResult[];
  done: number;
  refused: number;
  considered: number;
}

/* --- compliance (Phase 13, D-021) ------------------------------------- */

export interface ComplianceHit {
  chunk_id: string;
  document_id: string;
  document_title: string;
  section_reference: string | null;
  marketplace_code: string | null;
  content: string;
  score: number;
  signals: string[];
}

export interface ComplianceSearchResponse {
  query: string;
  hits: ComplianceHit[];
  signals: string[];
  candidates_considered: number;
  /** Always says this is evidence, not a compliance decision (D-005). */
  note: string;
}

export interface ComplianceDocumentRef {
  id: string;
  title: string;
  marketplace_code: string | null;
  document_kind: string | null;
  version_label: string | null;
  chunk_count: number;
  updated_at: string;
}

export interface ComplianceDocumentPage extends Page<ComplianceDocumentRef> {
  documents: ComplianceDocumentRef[];
}

/* --- session and local-only shapes ------------------------------------ */

export interface SessionUser {
  id: string;
  email: string;
  display_name: string | null;
}

export interface SessionWorkspace {
  id: string;
  name: string;
  slug: string;
  role: string;
}

export interface CurrentSession {
  user: SessionUser;
  workspaces: SessionWorkspace[];
  expires_at: string;
}

/**
 * Agent activity has no route (the graph is called from tests only), so this
 * shape exists for the fixture panel and nothing else. It is deliberately
 * separated from the rows above: nothing real is ever rendered into it.
 */
export type AgentState = "running" | "waiting" | "done" | "failed";

export interface AgentRun {
  id: string;
  node: string;
  state: AgentState;
  detail: string;
  started_at: string;
  duration_ms: number | null;
}

/** Composite risk bands for the catalog table, which the API does not return. */
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