/**
 * Fixture data for a console with no backend attached.
 *
 * Every value here is invented. The ids, timestamps, risk bands and compliance
 * citations are shaped like the API's but describe nothing that exists: no
 * workspace has these products, these sources or these rules. What they are for
 * is reviewing a layout, and every screen that renders them says so above the
 * fold (`reasonNotice` in `lib/notices.ts`).
 *
 * Rows are generated from small seed lists and sliced for pagination, so the
 * fixture path exercises the same `total`/`limit`/`offset` arithmetic the live
 * path does — an empty table here would hide a paging bug.
 */

import type {
  AgentRun,
  ApprovalQueuePage,
  CatalogSourcePage,
  ComplianceDocumentPage,
  ComplianceSearchResponse,
  ConflictPage,
  HealthScore,
  ProductPage,
  ProductRef,
  ProposalPage,
  ReconciliationRunPage,
} from "./types";

const CATALOG = "22222222-2222-4222-8222-222222222222";

/**
 * Deterministic ids: the same row has the same id on every render.
 *
 * Both halves vary with the seed because screens truncate ids to eight
 * characters (`CHG-0001 · a3f0c1d2`); a fixture whose ids all started with the
 * same eight zeros made every row look like it belonged to one product.
 */
function id(seed: number): string {
  // Knuth's multiplicative hash: consecutive seeds land far apart, so the
  // eight characters a screen shows are not the same on every row.
  const hashed = ((seed + 1) * 2654435761) >>> 0;
  const hex = hashed.toString(16).padStart(8, "0");
  return `${hex}-4000-8000-${hex}${hex}`;
}

function hoursAgo(hours: number): string {
  const base = Date.parse("2026-10-07T09:00:00Z");
  return new Date(base - hours * 3600_000).toISOString();
}

interface Seed {
  sku: string;
  title: string;
  brand: string | null;
  category: string;
  status: string;
  isParent?: boolean;
  attributes?: Record<string, unknown>;
  gtin?: string;
  upc?: string;
}

const PRODUCT_SEEDS: Seed[] = [
  {
    sku: "CG-1001",
    title: "Navy Ceramic Mug 350ml",
    brand: "Kettle & Co",
    category: "Home & Kitchen > Drinkware > Mugs",
    status: "active",
    gtin: "5012345678900",
    attributes: { material: "ceramic", capacity_ml: 350, dishwasher_safe: true },
  },
  {
    sku: "CG-1002",
    title: "Cast Iron Skillet 26cm",
    brand: "Kettle & Co",
    category: "Home & Kitchen > Cookware > Pans",
    status: "active",
    gtin: "5012345678901",
    attributes: { material: "cast iron", diameter_cm: 26 },
  },
  {
    sku: "CG-1003",
    title: "Merino Wool Throw Blanket",
    brand: "Northfield",
    category: "Home & Textiles > Bedding > Throws",
    status: "active",
    attributes: { material: "merino wool", size: "130x180cm", colour: "charcoal" },
  },
  {
    sku: "CG-1004",
    title: "Travel Mug 400ml — Bundle of 2",
    brand: "Kettle & Co",
    category: "Home & Kitchen > Drinkware > Mugs",
    status: "draft",
    isParent: true,
    attributes: { bundle_size: 2 },
  },
  {
    sku: "CG-1005",
    title: "Travel Mug 400ml — Graphite",
    brand: "Kettle & Co",
    category: "Home & Kitchen > Drinkware > Mugs",
    status: "suppressed",
    attributes: { colour: "graphite", capacity_ml: 400 },
  },
  {
    sku: "CG-1006",
    title: "Bamboo Chopping Board Large",
    brand: "Northfield",
    category: "Home & Kitchen > Prep > Boards",
    status: "active",
    upc: "012345678905",
    attributes: { material: "bamboo", length_cm: 38 },
  },
  {
    sku: "CG-1007",
    title: "Linen Napkin Set of 4",
    brand: "Atelier Verve",
    category: "Home & Textiles > Dining > Napkins",
    status: "active",
    attributes: { material: "linen", count: 4, colour: "sand" },
  },
  {
    sku: "CG-1008",
    title: "Espresso Cups (Set of 6)",
    brand: "Atelier Verve",
    category: "Home & Kitchen > Drinkware > Cups",
    status: "archived",
    attributes: { count: 6, capacity_ml: 70 },
  },
  {
    sku: "CG-1009",
    title: "Walnut Serving Board",
    brand: "Northfield",
    category: "Home & Kitchen > Prep > Boards",
    status: "active",
    attributes: { material: "walnut", length_cm: 45 },
  },
  {
    sku: "CG-1010",
    title: "Stoneware Dinner Plate Set",
    brand: "Kettle & Co",
    category: "Home & Kitchen > Tableware > Plates",
    status: "active",
    attributes: { material: "stoneware", count: 4 },
  },
];

function productAt(index: number): ProductRef {
  const seed = PRODUCT_SEEDS[index % PRODUCT_SEEDS.length];
  const cycle = Math.floor(index / PRODUCT_SEEDS.length);
  const isChild = Boolean(seed.isParent) && index % 3 === 1;
  return {
    id: id(index + 1),
    catalog_id: CATALOG,
    sku: cycle === 0 ? seed.sku : `${seed.sku}-${cycle + 1}`,
    gtin: seed.gtin ?? null,
    upc: seed.upc ?? null,
    mpn: null,
    brand: seed.brand,
    manufacturer: seed.brand,
    title: seed.title,
    category_path: seed.category,
    status: isChild ? "active" : seed.status,
    version: 1 + (index % 4),
    is_parent: Boolean(seed.isParent) && !isChild,
    attributes: { ...(seed.attributes ?? {}), source_row: index + 1 },
    last_reconciled_at: hoursAgo(index + 2),
    created_at: hoursAgo(72 + index * 3),
  };
}

const PRODUCT_TOTAL = 240;

export function products(limit: number, offset: number): ProductPage {
  const slice = Array.from({ length: Math.max(0, Math.min(limit, PRODUCT_TOTAL - offset)) }, (_, position) =>
    productAt(offset + position),
  );
  return { products: slice, items: slice, total: PRODUCT_TOTAL, limit, offset };
}

interface ConflictSeed {
  field: string;
  type: string;
  values: Record<string, { value: unknown; rank: number | null }>;
  confidence: number;
}

const CONFLICT_SEEDS: ConflictSeed[] = [
  {
    field: "title",
    type: "attribute_mismatch",
    values: {
      erp: { value: "Navy Ceramic Mug 350ml", rank: 3 },
      pim: { value: "Navy Ceramic Mug 350 ml", rank: 1 },
      manufacturer: { value: "Ceramic Mug Navy 350ml", rank: 2 },
    },
    confidence: 0.91,
  },
  {
    field: "capacity_ml",
    type: "attribute_mismatch",
    values: {
      erp: { value: 350, rank: 2 },
      manufacturer: { value: "350", rank: null },
    },
    confidence: 0.64,
  },
  {
    field: "material",
    type: "attribute_mismatch",
    values: {
      erp: { value: "ceramic", rank: 1 },
      supplier_a: { value: "stoneware", rank: 4 },
    },
    confidence: 0.83,
  },
  {
    field: "colour",
    type: "identity_mismatch",
    values: {
      pim: { value: "graphite", rank: null },
      erp: { value: "charcoal", rank: null },
    },
    confidence: 0.42,
  },
  {
    field: "care_instructions",
    type: "missing_attribute",
    values: {
      pim: { value: "Dishwasher safe, microwave safe", rank: 1 },
    },
    confidence: 0.71,
  },
  {
    field: "package_dimensions",
    type: "missing_attribute",
    values: {
      erp: { value: "12 x 9 x 11 cm", rank: 2 },
    },
    confidence: 0.55,
  },
];

const CONFLICT_TOTAL = 68;

export function conflicts(limit: number, offset: number, productId?: string): ConflictPage {
  const slice = Array.from({ length: Math.max(0, Math.min(limit, CONFLICT_TOTAL - offset)) }, (_, position) => {
    const index = offset + position;
    const seed = CONFLICT_SEEDS[index % CONFLICT_SEEDS.length];
    return {
      id: id(1000 + index),
      product_id: productId ?? id((index % PRODUCT_TOTAL) + 1),
      conflict_type: seed.type,
      field_name: seed.field,
      competing_values: seed.values as unknown as Record<string, unknown>,
      confidence: seed.confidence,
      reconciliation_run_id: id(2000 + (index % 9)),
      resolved_at: null,
      created_at: hoursAgo(index + 1),
    };
  });
  return { conflicts: slice, items: slice, total: CONFLICT_TOTAL, limit, offset };
}

interface ProposalSeed {
  field: string;
  current: unknown;
  proposed: unknown;
  reason: string;
  confidence: number;
  risk: string;
  requiresApproval: boolean;
  validation: string;
}

const PROPOSAL_SEEDS: ProposalSeed[] = [
  {
    field: "title",
    current: "Navy Ceramic Mug 350ml",
    proposed: "Navy Ceramic Mug 350 ml",
    reason: "Manufacturer title feed disagrees with the PIM title; PIM wins the authority order (D-002).",
    confidence: 0.91,
    risk: "LOW",
    requiresApproval: true,
    validation: "validated",
  },
  {
    field: "material",
    current: "ceramic",
    proposed: "stoneware",
    reason: "Supplier A lists stoneware; the PIM says ceramic. Authority picks ceramic, so this needs a human.",
    confidence: 0.62,
    risk: "HIGH",
    requiresApproval: true,
    validation: "pending",
  },
  {
    field: "care_instructions",
    current: null,
    proposed: "Dishwasher safe, microwave safe",
    reason: "The PIM carries care instructions the ERP never ingested.",
    confidence: 0.77,
    risk: "MEDIUM",
    requiresApproval: true,
    validation: "validated",
  },
  {
    field: "package_dimensions",
    current: null,
    proposed: "12 x 9 x 11 cm",
    reason: "Missing from the ERP feed; supplier A provided the value with a packing list.",
    confidence: 0.55,
    risk: "LOW",
    requiresApproval: true,
    validation: "pending",
  },
  {
    field: "colour",
    current: "graphite",
    proposed: "charcoal",
    reason: "Two sources disagree with equal authority; nobody can choose this one automatically.",
    confidence: 0.38,
    risk: "CRITICAL",
    requiresApproval: true,
    validation: "pending",
  },
  {
    field: "capacity_ml",
    current: 350,
    proposed: "350",
    reason: "Type disagreement, not a value disagreement: the ERP holds an integer, the feed a string.",
    confidence: 0.88,
    risk: "LOW",
    requiresApproval: true,
    validation: "validated",
  },
];

const PROPOSAL_TOTAL = 41;

export function proposals(limit: number, offset: number, riskLevel?: string): ProposalPage {
  const filtered = PROPOSAL_SEEDS.filter((seed) => !riskLevel || seed.risk === riskLevel);
  const total = riskLevel ? Math.max(filtered.length, 6) : PROPOSAL_TOTAL;
  const slice = Array.from({ length: Math.max(0, Math.min(limit, total - offset)) }, (_, position) => {
    const index = offset + position;
    const seed = filtered[index % Math.max(filtered.length, 1)];
    return {
      id: id(3000 + index),
      change_ref: `CHG-${(index + 1).toString().padStart(4, "0")}`,
      product_id: id((index % PRODUCT_TOTAL) + 1),
      field_name: seed.field,
      current_value: seed.current,
      proposed_value: seed.proposed,
      reason: seed.reason,
      confidence: seed.confidence,
      risk_level: seed.risk,
      requires_approval: seed.requiresApproval,
      status: "pending_approval",
      validation_status: seed.validation,
      seo_impact: seed.field === "title" ? "title tag changes; review the search snippet" : "none",
      compliance_impact: seed.field === "material" ? "material claims are regulated in 3 marketplaces" : "none",
      affected_channels: seed.risk === "CRITICAL" ? ["amazon", "shopify", "tiktok"] : ["shopify"],
      created_at: hoursAgo(index + 1),
    };
  });
  return { proposals: slice, items: slice, total, limit, offset };
}

const APPROVAL_TOTAL = 17;

export function approvals(limit: number, offset: number, riskLevel?: string): ApprovalQueuePage {
  const page = proposals(limit, offset, riskLevel);
  const items = page.proposals.map((proposal, position) => ({
    proposal,
    required_approvals: proposal.risk_level === "CRITICAL" ? 2 : 1,
    approvals_received: 0,
    request_status: "pending_approval",
    simulated: proposal.validation_status === "validated" && position % 3 !== 2,
  }));
  return { items, total: riskLevel ? page.total : APPROVAL_TOTAL, limit, offset };
}

export function sources(): CatalogSourcePage {
  const rows = [
    {
      id: id(4001),
      name: "ERP product export (weekly)",
      kind: "csv",
      status: "processed",
      record_count: 4820,
      created_at: hoursAgo(96),
    },
    {
      id: id(4002),
      name: "PIM live feed",
      kind: "api",
      status: "active",
      record_count: 5190,
      created_at: hoursAgo(240),
    },
    {
      id: id(4003),
      name: "Supplier A spreadsheet",
      kind: "xlsx",
      status: "processed",
      record_count: 1204,
      created_at: hoursAgo(30),
    },
    {
      id: id(4004),
      name: "Manufacturer title feed",
      kind: "csv",
      status: "failed",
      record_count: 0,
      created_at: hoursAgo(6),
    },
  ];
  return { sources: rows, items: rows, total: rows.length, limit: rows.length, offset: 0 };
}

export function runs(): ReconciliationRunPage {
  const rows = [
    {
      id: id(5001),
      catalog_id: CATALOG,
      catalog_version: 14,
      status: "completed",
      products_analyzed: 4820,
      conflicts_found: 68,
      started_at: hoursAgo(5),
      completed_at: hoursAgo(4),
    },
    {
      id: id(5002),
      catalog_id: CATALOG,
      catalog_version: 13,
      status: "completed",
      products_analyzed: 4780,
      conflicts_found: 71,
      started_at: hoursAgo(29),
      completed_at: hoursAgo(28),
    },
    {
      id: id(5003),
      catalog_id: CATALOG,
      catalog_version: 12,
      status: "failed",
      products_analyzed: 2011,
      conflicts_found: 12,
      started_at: hoursAgo(53),
      completed_at: hoursAgo(53),
    },
  ];
  return { runs: rows, items: rows, total: rows.length, limit: rows.length, offset: 0 };
}

const DOCUMENTS = [
  {
    id: id(6001),
    title: "Amazon UK Listing Requirements",
    marketplace_code: "amazon",
    document_kind: "marketplace_policy",
    version_label: "2026-07",
    chunk_count: 214,
    updated_at: hoursAgo(24 * 9),
  },
  {
    id: id(6002),
    title: "Shopify Product Taxonomy",
    marketplace_code: "shopify",
    document_kind: "category_reference",
    version_label: "2026-06",
    chunk_count: 96,
    updated_at: hoursAgo(24 * 21),
  },
  {
    id: id(6003),
    title: "Internal Claims & Substantiation Policy",
    marketplace_code: null,
    document_kind: "internal_policy",
    version_label: "3.1",
    chunk_count: 58,
    updated_at: hoursAgo(24 * 40),
  },
];

/**
 * A local search over the fixture corpus, ranked by how many query words a
 * chunk contains. It exists so the compliance page can be reviewed with
 * citations; the real ranking is `ts_rank` plus vector distance (D-021), and a
 * screen that presented this as retrieval would be lying about which pass ran.
 */
export function search(q: string): ComplianceSearchResponse {
  const terms = q.toLowerCase().split(/\s+/).filter((term) => term !== "");
  const chunks = [
    {
      chunk_id: id(7001),
      document_id: id(6001),
      document_title: "Amazon UK Listing Requirements",
      section_reference: "Section 4.2",
      marketplace_code: "amazon",
      content:
        "Product titles must not exceed 200 characters including spaces, and must not contain promotional phrases, pricing or availability claims.",
    },
    {
      chunk_id: id(7002),
      document_id: id(6001),
      document_title: "Amazon UK Listing Requirements",
      section_reference: "Section 7.1",
      marketplace_code: "amazon",
      content:
        "Claims about materials must be substantiated by the brand. Unsubstantiated material claims are a listing suspension risk.",
    },
    {
      chunk_id: id(7003),
      document_id: id(6003),
      document_title: "Internal Claims & Substantiation Policy",
      section_reference: "Section 12.3",
      marketplace_code: null,
      content:
        "A product attribute may only be restated from the highest-authority source available. Where two sources tie, a human decides.",
    },
    {
      chunk_id: id(7004),
      document_id: id(6002),
      document_title: "Shopify Product Taxonomy",
      section_reference: "Section 3.4",
      marketplace_code: "shopify",
      content:
        "Category paths must be set from the current taxonomy. A deprecated category path resolves to no category at all.",
    },
  ];
  const scored = chunks
    .map((chunk) => {
      const haystack = `${chunk.section_reference} ${chunk.content}`.toLowerCase();
      const hits = terms.filter((term) => haystack.includes(term)).length;
      return { chunk, score: terms.length === 0 ? 0 : hits / terms.length, hits };
    })
    .filter((entry) => entry.hits > 0)
    .sort((a, b) => b.score - a.score);
  return {
    query: q,
    hits: scored.slice(0, 8).map((entry) => ({
      ...entry.chunk,
      score: Number(entry.score.toFixed(4)),
      signals: ["fixture-corpus", "keyword-overlap"],
    })),
    signals: ["fixture-corpus"],
    candidates_considered: chunks.length,
    note:
      "Fixture corpus ranked by word overlap. Evidence for a recommendation, not a compliance decision.",
  };
}

export function documents(): ComplianceDocumentPage {
  return { documents: DOCUMENTS, items: DOCUMENTS, total: DOCUMENTS.length, limit: DOCUMENTS.length, offset: 0 };
}

/**
 * Health meters have no route: the composite score is a UI idea the backend
 * never computed. Kept as a fixture, labelled as such, and the dashboard shows
 * it in its own panel rather than presenting it beside real counts.
 */
export const health: HealthScore = {
  overall: 72,
  data_quality: 81,
  consistency: 64,
  compliance: 77,
  relationship_integrity: 88,
  seo_readiness: 58,
  change_safety: 69,
  computed_at: "2026-10-07T09:00:00Z",
};

/** No route reports agent runs; the graph is only invoked by tests. */
export const agentRuns: AgentRun[] = [
  {
    id: id(8001),
    node: "intake",
    state: "done",
    detail: "Parsed 1204 rows from Supplier A spreadsheet (fixture)",
    started_at: hoursAgo(3),
    duration_ms: 1840,
  },
  {
    id: id(8002),
    node: "analyze",
    state: "done",
    detail: "Scored 6 proposals, escalated 1 (fixture)",
    started_at: hoursAgo(3),
    duration_ms: 6120,
  },
  {
    id: id(8003),
    node: "finalize",
    state: "running",
    detail: "Fixture row: no run is in flight because nothing dispatches the graph",
    started_at: hoursAgo(0),
    duration_ms: null,
  },
];

export const workspaceName = "Fixture workspace (not real)";