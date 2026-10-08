/**
 * The reconciliation workspace's three-panel model (§30, D-015).
 *
 * Source → Truth → Analysis is not a layout, it is the audit trail: for one
 * product and one attribute, what each source said, which value won and why,
 * and what that disagreement turned into. This module assembles that from the
 * shapes the API actually returns — conflicts carry the competing values,
 * proposals carry the winning value and its reasoning — rather than inventing
 * a `provenance` endpoint that does not exist.
 *
 * Pure, so the panel's rules are unit tested: a value with no source is never
 * presented as authoritative, and a conflict with no winner is shown as a tie
 * instead of being silently resolved.
 */

import type { ChangeProposalRef, CompetingValues, ConflictRef, ProductRef } from "./types";

export interface SourceClaim {
  /** The source as the backend named it, e.g. `erp`, `pim`, `manufacturer`. */
  source: string;
  value: unknown;
  /** Authority rank from D-002; lower wins. `null` when unranked. */
  rank: number | null;
}

export interface AttributePanel {
  field: string;
  claims: SourceClaim[];
  /** The value the authority rule settled on, or `null` when nobody won. */
  resolvedValue: unknown;
  resolution: "authority" | "tie" | "single-source" | "missing";
  /** The proposal this attribute produced, when one exists. */
  proposal: ChangeProposalRef | null;
  conflictIds: string[];
  riskLevel: string | null;
}

export interface ProductPanel {
  product: ProductRef;
  attributes: AttributePanel[];
  conflictCount: number;
  proposalCount: number;
  /** Fields where sources disagree and nothing settled it. */
  ties: string[];
}

/**
 * Turn one `competing_values` bag into ranked claims.
 *
 * The wire shape is a JSON object whose values vary with the conflict type
 * (`{"erp": {"value": "x", "rank": 1}}` for an attribute mismatch, a bare
 * string in other cases), so parsing is defensive on purpose: a shape this code
 * does not recognise becomes one unranked claim rather than a dropped row.
 */
export function parseClaims(competing: CompetingValues | undefined | null): SourceClaim[] {
  if (!competing || typeof competing !== "object") return [];
  const claims: SourceClaim[] = [];
  for (const [source, raw] of Object.entries(competing)) {
    if (raw !== null && typeof raw === "object" && !Array.isArray(raw)) {
      const bag = raw as Record<string, unknown>;
      const rank = typeof bag.rank === "number" ? bag.rank : null;
      claims.push({ source, value: bag.value ?? null, rank });
    } else {
      claims.push({ source, value: raw, rank: null });
    }
  }
  return claims.sort((a, b) => (a.rank ?? Number.MAX_SAFE_INTEGER) - (b.rank ?? Number.MAX_SAFE_INTEGER));
}

/**
 * Which value wins, per D-002.
 *
 * Authority first (lowest rank), then a proposal's proposed value — the
 * deterministic pipeline has already decided, and re-deciding here would let
 * the screen disagree with the engine. A tie is a tie: the panel says so and
 * the operator decides, which is D-020's whole point.
 */
export function resolveAttribute(
  field: string,
  conflicts: readonly ConflictRef[],
  proposals: readonly ChangeProposalRef[],
): AttributePanel {
  const fieldConflicts = conflicts.filter((conflict) => conflict.field_name === field);
  const claims = fieldConflicts.flatMap((conflict) => parseClaims(conflict.competing_values));
  const proposal = proposals.find((candidate) => candidate.field_name === field) ?? null;
  const ranked = claims.filter((claim) => claim.rank !== null);
  let resolution: AttributePanel["resolution"];
  let resolvedValue: unknown;
  if (ranked.length === 0) {
    // Nothing carries an authority rank: one claim is simply the only thing
    // anyone said, and several unranked claims that disagree are a tie.
    if (claims.length === 1) {
      resolution = "single-source";
      resolvedValue = claims[0]?.value ?? null;
    } else if (claims.length > 1) {
      resolution = "tie";
      resolvedValue = null;
    } else {
      resolution = "missing";
      resolvedValue = null;
    }
  } else {
    const best = Math.min(...ranked.map((claim) => claim.rank as number));
    const winners = ranked.filter((claim) => claim.rank === best);
    if (winners.length === 1) {
      resolution = "authority";
      resolvedValue = winners[0]?.value ?? null;
    } else {
      // Two sources at the same authority rank: D-002 does not break this and
      // neither does this panel. A winner here would be an unapproved change.
      resolution = "tie";
      resolvedValue = null;
    }
  }

  return {
    field,
    claims,
    resolvedValue: proposal ? proposal.proposed_value : resolvedValue,
    resolution,
    proposal,
    conflictIds: fieldConflicts.map((conflict) => conflict.id),
    riskLevel: proposal?.risk_level ?? null,
  };
}

export function buildProductPanel(
  product: ProductRef,
  conflicts: readonly ConflictRef[],
  proposals: readonly ChangeProposalRef[],
): ProductPanel {
  const productConflicts = conflicts.filter((conflict) => conflict.product_id === product.id);
  const productProposals = proposals.filter((proposal) => proposal.product_id === product.id);
  const fields = Array.from(
    new Set([
      ...productConflicts.map((conflict) => conflict.field_name),
      ...productProposals.map((proposal) => proposal.field_name),
    ]),
  ).sort();
  const attributes = fields.map((field) => resolveAttribute(field, productConflicts, productProposals));
  return {
    product,
    attributes,
    conflictCount: productConflicts.length,
    proposalCount: productProposals.length,
    ties: attributes.filter((panel) => panel.resolution === "tie").map((panel) => panel.field),
  };
}

/**
 * Attributes the workspace must show even when nothing disagrees about them:
 * the identifiers and the title. A canvas that only renders conflicts makes an
 * agreed value look like an absent one.
 */
export function canonicalAttributes(product: ProductRef): { field: string; value: unknown }[] {
  const attributes: { field: string; value: unknown }[] = [];
  if (product.title) attributes.push({ field: "title", value: product.title });
  if (product.brand) attributes.push({ field: "brand", value: product.brand });
  if (product.manufacturer) attributes.push({ field: "manufacturer", value: product.manufacturer });
  if (product.category_path) attributes.push({ field: "category_path", value: product.category_path });
  for (const [field, value] of Object.entries(product.attributes ?? {})) {
    attributes.push({ field, value });
  }
  for (const [field, value] of Object.entries({
    gtin: product.gtin,
    upc: product.upc,
    mpn: product.mpn,
  })) {
    if (value) attributes.push({ field, value });
  }
  return attributes;
}