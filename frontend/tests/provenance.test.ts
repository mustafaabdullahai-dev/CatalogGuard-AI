import { describe, expect, it } from "vitest";

import { buildProductPanel, canonicalAttributes, parseClaims, resolveAttribute } from "@/lib/provenance";
import type { ChangeProposalRef, ConflictRef, ProductRef } from "@/lib/types";

function product(overrides: Partial<ProductRef> = {}): ProductRef {
  return {
    id: "p-1",
    catalog_id: "c-1",
    sku: "CG-1001",
    gtin: "5012345678900",
    upc: null,
    mpn: null,
    brand: "Kettle & Co",
    manufacturer: "Kettle & Co",
    title: "Navy Mug",
    category_path: "Home > Mugs",
    status: "active",
    version: 1,
    is_parent: false,
    attributes: { colour: "navy" },
    last_reconciled_at: null,
    created_at: "2026-10-01T00:00:00Z",
    ...overrides,
  };
}

function conflict(overrides: Partial<ConflictRef> = {}): ConflictRef {
  return {
    id: "cf-1",
    product_id: "p-1",
    conflict_type: "attribute_mismatch",
    field_name: "title",
    competing_values: {
      erp: { value: "Navy Ceramic Mug 350ml", rank: 3 },
      pim: { value: "Navy Ceramic Mug 350 ml", rank: 1 },
    },
    confidence: 0.9,
    reconciliation_run_id: null,
    resolved_at: null,
    created_at: "2026-10-02T00:00:00Z",
    ...overrides,
  };
}

function proposal(overrides: Partial<ChangeProposalRef> = {}): ChangeProposalRef {
  return {
    id: "pr-1",
    change_ref: "CHG-0001",
    product_id: "p-1",
    field_name: "title",
    current_value: "Navy Ceramic Mug 350ml",
    proposed_value: "Navy Ceramic Mug 350 ml",
    reason: "PIM wins the authority order",
    confidence: 0.91,
    risk_level: "LOW",
    requires_approval: true,
    status: "pending_approval",
    validation_status: "validated",
    seo_impact: "none",
    compliance_impact: "none",
    affected_channels: [],
    created_at: "2026-10-03T00:00:00Z",
    ...overrides,
  };
}

describe("parseClaims", () => {
  it("reads ranked claims and sorts them best-rank first", () => {
    const claims = parseClaims({ erp: { value: "a", rank: 3 }, pim: { value: "b", rank: 1 } });
    expect(claims.map((claim) => claim.source)).toEqual(["pim", "erp"]);
    expect(claims[0]).toEqual({ source: "pim", value: "b", rank: 1 });
  });

  it("keeps unranked values rather than dropping them", () => {
    const claims = parseClaims({ colour: "navy", erp: { value: "grey", rank: 2 } });
    expect(claims).toContainEqual({ source: "colour", value: "navy", rank: null });
  });

  it("sorts unranked claims last", () => {
    const claims = parseClaims({ a: { value: 1, rank: 2 }, b: { value: 2, rank: null } });
    expect(claims[0]?.source).toBe("a");
  });

  it("returns nothing for an empty or non-object bag", () => {
    expect(parseClaims({})).toEqual([]);
    expect(parseClaims(null)).toEqual([]);
    expect(parseClaims(undefined)).toEqual([]);
  });
});

describe("resolveAttribute", () => {
  it("picks the lowest authority rank and calls it authority-resolved", () => {
    const panel = resolveAttribute("title", [conflict()], []);
    expect(panel.resolution).toBe("authority");
    expect(panel.resolvedValue).toBe("Navy Ceramic Mug 350 ml");
    expect(panel.conflictIds).toEqual(["cf-1"]);
  });

  it("reports a tie rather than picking one", () => {
    const panel = resolveAttribute(
      "colour",
      [
        conflict({
          id: "cf-2",
          field_name: "colour",
          competing_values: { erp: { value: "charcoal", rank: 2 }, pim: { value: "graphite", rank: 2 } },
        }),
      ],
      [],
    );
    expect(panel.resolution).toBe("tie");
    expect(panel.resolvedValue).toBeNull();
  });

  it("treats a single unranked claim as single-source, not as a winner", () => {
    const panel = resolveAttribute(
      "care_instructions",
      [
        conflict({
          id: "cf-3",
          field_name: "care_instructions",
          competing_values: { pim: "Dishwasher safe" },
        }),
      ],
      [],
    );
    expect(panel.resolution).toBe("single-source");
    expect(panel.resolvedValue).toBe("Dishwasher safe");
  });

  it("reports a field with no claims as missing", () => {
    const panel = resolveAttribute("brand", [], []);
    expect(panel.resolution).toBe("missing");
    expect(panel.resolvedValue).toBeNull();
  });

  it("uses the proposal's value when the pipeline already decided one", () => {
    const panel = resolveAttribute("title", [conflict()], [proposal({ proposed_value: "Decided by the engine" })]);
    expect(panel.resolvedValue).toBe("Decided by the engine");
    expect(panel.proposal?.change_ref).toBe("CHG-0001");
    expect(panel.riskLevel).toBe("LOW");
  });
});

describe("buildProductPanel", () => {
  it("groups conflicts and proposals by attribute", () => {
    const panel = buildProductPanel(
      product(),
      [conflict(), conflict({ id: "cf-2", field_name: "material", competing_values: { erp: { value: "steel", rank: 1 } } })],
      [proposal()],
    );
    expect(panel.attributes.map((attribute) => attribute.field)).toEqual(["material", "title"]);
    expect(panel.conflictCount).toBe(2);
    expect(panel.proposalCount).toBe(1);
  });

  it("lists tied fields separately so the canvas can show them as unresolved", () => {
    const panel = buildProductPanel(
      product(),
      [
        conflict({
          id: "cf-9",
          field_name: "colour",
          competing_values: { erp: { value: "charcoal", rank: 1 }, pim: { value: "graphite", rank: 1 } },
        }),
      ],
      [],
    );
    expect(panel.ties).toEqual(["colour"]);
  });

  it("ignores conflicts and proposals for other products", () => {
    const panel = buildProductPanel(product(), [conflict({ product_id: "p-2" })], [proposal({ product_id: "p-2" })]);
    expect(panel.attributes).toEqual([]);
    expect(panel.conflictCount).toBe(0);
  });
});

describe("canonicalAttributes", () => {
  it("includes identifiers and catalog fields, then extra attributes", () => {
    const fields = canonicalAttributes(product()).map((attribute) => attribute.field);
    expect(fields).toContain("title");
    expect(fields).toContain("brand");
    expect(fields).toContain("category_path");
    expect(fields).toContain("gtin");
    expect(fields).toContain("colour");
  });

  it("omits identifiers that are null instead of printing them", () => {
    const fields = canonicalAttributes(product({ upc: null, mpn: null })).map((attribute) => attribute.field);
    expect(fields).not.toContain("upc");
    expect(fields).not.toContain("mpn");
  });
});