/**
 * The sandbox's §19 diff and §18 impact report, parsed for display.
 *
 * Both arrive as free-form JSON bags, so this module is defensive: a field
 * whose shape is not one it recognises is dropped rather than rendered as
 * `[object Object]`, and a missing report renders as "not reported" instead of
 * an empty section that looks like good news.
 *
 * Pure, so the parsing rules are unit tested against the shapes the sandbox
 * actually produces (`app/services/simulation/sandbox.py`).
 */

export type Indicator = "unchanged" | "added" | "removed" | "modified";

export interface DiffRow {
  field: string;
  current: string;
  proposed: string;
  indicator: Indicator;
  risky: boolean;
  requiresApproval: boolean;
}

export interface DiffView {
  rows: DiffRow[];
  changed: number;
  tree: { before: string; after: string } | null;
}

export interface ImpactView {
  affectedChannels: string[];
  affectedProducts: number | null;
  relationshipValid: boolean | null;
  conflictsRemaining: number | null;
  disagreeing: string[];
  risk: { level: string; requiresApproval: boolean } | null;
  recommendation: string | null;
  relationshipFindings: { before: Record<string, number>; after: Record<string, number> } | null;
}

/** Anything renders as text; objects and arrays as compact JSON. */
export function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value === "" ? "—" : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function indicatorOf(value: unknown): Indicator {
  return value === "added" || value === "removed" || value === "modified" ? value : "unchanged";
}

function recordOf(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function countOf(value: unknown): Record<string, number> {
  const bag = recordOf(value);
  if (!bag) return {};
  const counts: Record<string, number> = {};
  for (const [key, raw] of Object.entries(bag)) {
    if (typeof raw === "number") counts[key] = raw;
  }
  return counts;
}

export function parseDiff(diff: Record<string, unknown> | null | undefined): DiffView {
  const rows: DiffRow[] = [];
  const rawRows = Array.isArray(diff?.rows) ? (diff?.rows as unknown[]) : [];
  for (const raw of rawRows) {
    const row = recordOf(raw);
    if (!row || typeof row.field !== "string") continue;
    rows.push({
      field: row.field,
      current: displayValue(row.current),
      proposed: displayValue(row.proposed),
      indicator: indicatorOf(row.indicator),
      risky: row.risky === true,
      requiresApproval: row.requires_approval === true,
    });
  }
  const tree = recordOf(diff?.tree);
  const before = recordOf(tree?.before);
  const after = recordOf(tree?.after);
  return {
    rows,
    changed: rows.filter((row) => row.indicator !== "unchanged").length,
    tree: before && after ? { before: displayValue(before), after: displayValue(after) } : null,
  };
}

export function parseImpact(impact: Record<string, unknown> | null | undefined): ImpactView {
  const bag = recordOf(impact);
  const risk = recordOf(bag?.risk);
  const findings = recordOf(bag?.relationship_findings);
  return {
    affectedChannels: Array.isArray(bag?.affected_channels)
      ? (bag?.affected_channels as unknown[]).map((entry) => displayValue(entry))
      : [],
    affectedProducts: typeof bag?.affected_products === "number" ? bag.affected_products : null,
    relationshipValid:
      typeof bag?.relationship_valid === "boolean" ? bag.relationship_valid : null,
    conflictsRemaining:
      typeof bag?.conflicts_remaining === "number" ? bag.conflicts_remaining : null,
    disagreeing: Array.isArray(bag?.sources_still_disagreeing)
      ? (bag?.sources_still_disagreeing as unknown[]).map((entry) => displayValue(entry))
      : [],
    risk:
      risk && typeof risk.level === "string"
        ? { level: risk.level, requiresApproval: risk.requires_approval === true }
        : null,
    recommendation: typeof bag?.recommendation === "string" ? bag.recommendation : null,
    relationshipFindings: findings
      ? { before: countOf(findings.before), after: countOf(findings.after) }
      : null,
  };
}

/** Stage status wording (§60's status vocabulary), shared with badges. */
export const STAGE_LABELS: Record<string, string> = {
  passed: "passed",
  failed: "failed",
  skipped: "not run",
  flagged: "flagged",
  not_configured: "not configured",
};

export function stageLabel(status: string): string {
  return STAGE_LABELS[status] ?? status.replace(/_/g, " ");
}

export function stageTone(status: string): "ok" | "bad" | "warn" | "mute" {
  if (status === "passed") return "ok";
  if (status === "failed") return "bad";
  if (status === "flagged") return "warn";
  return "mute";
}

/**
 * A `null` verdict is not a pass.
 *
 * §18 is explicit that a stage which could not run must not read as `passed`,
 * and the sandbox encodes that as `null`. Rendering it as a green tick is the
 * one mistake this screen cannot make.
 */
export function verdictLabel(value: boolean | null | undefined): { text: string; tone: "ok" | "bad" | "mute" } {
  if (value === true) return { text: "valid", tone: "ok" };
  if (value === false) return { text: "invalid", tone: "bad" };
  return { text: "not run", tone: "mute" };
}