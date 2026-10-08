import { describe, expect, it } from "vitest";

import { FIXTURE_NOTICES, reasonNotice } from "@/lib/notices";
import * as fixtures from "@/lib/fixtures";

/**
 * The fixture path is what an operator sees when no backend is reachable, so it
 * has two obligations: say so (never "no data"), and never be shaped unlike the
 * real response. The second is checked here against the same field names the
 * backend schemas use, because a drifted fixture renders a broken screen long
 * before anyone connects a workspace.
 */
describe("reasonNotice", () => {
  it("says nothing when the data is live", () => {
    expect(reasonNotice("live", null)).toBeNull();
  });

  it("names the cause for every fallback reason", () => {
    for (const reason of ["disabled", "offline", "unreachable", "forbidden", "server-error"]) {
      const notice = reasonNotice("fixture", reason);
      expect(notice).toBe(FIXTURE_NOTICES[reason]);
      expect(notice?.toLowerCase()).toContain("fixture");
    }
  });

  it("does not claim an empty workspace when the connection failed", () => {
    expect(reasonNotice("fixture", "unreachable")).not.toMatch(/no data|nothing/i);
  });

  it("falls back to the offline wording for an unknown reason", () => {
    expect(reasonNotice("fixture", "who-knows")).toBe(FIXTURE_NOTICES.offline);
    expect(reasonNotice("fixture", null)).toBe(FIXTURE_NOTICES.offline);
  });
});

describe("fixtures match the API response shapes", () => {
  it("pages products with the total, limit and offset the API returns", () => {
    const page = fixtures.products(25, 50);
    expect(page.products).toHaveLength(25);
    expect(page.limit).toBe(25);
    expect(page.offset).toBe(50);
    expect(page.total).toBeGreaterThan(75);
    const product = page.products[0]!;
    for (const field of [
      "id",
      "catalog_id",
      "sku",
      "gtin",
      "upc",
      "mpn",
      "brand",
      "manufacturer",
      "title",
      "category_path",
      "status",
      "version",
      "is_parent",
      "attributes",
      "last_reconciled_at",
      "created_at",
    ]) {
      expect(product).toHaveProperty(field);
    }
  });

  it("shortens the last page instead of inventing rows past the total", () => {
    const page = fixtures.products(50, fixtures.products(1, 0).total - 5);
    expect(page.products.length).toBeLessThanOrEqual(5);
  });

  it("exposes both the named and the items keys the list routes return", () => {
    expect(fixtures.conflicts(10, 0).conflicts).toHaveLength(10);
    expect(fixtures.proposals(10, 0).proposals).toHaveLength(10);
    expect(fixtures.approvals(10, 0).items).toHaveLength(10);
  });

  it("marks approvals as simulated only when validation says so", () => {
    const items = fixtures.approvals(10, 0).items;
    for (const item of items) {
      if (item.simulated) expect(item.proposal.validation_status).toBe("validated");
      expect(item.required_approvals).toBe(item.proposal.risk_level === "CRITICAL" ? 2 : 1);
    }
  });

  it("filters proposals by the risk band the API filters on", () => {
    const page = fixtures.proposals(10, 0, "CRITICAL");
    expect(page.proposals.every((proposal) => proposal.risk_level === "CRITICAL")).toBe(true);
  });

  it("cites the fixture compliance hits and says they are not a decision", () => {
    const result = fixtures.search("material claims");
    expect(result.hits.length).toBeGreaterThan(0);
    expect(result.hits.every((hit) => hit.section_reference)).toBe(true);
    expect(result.note).toMatch(/not a compliance decision/);
  });

  it("returns no hits for a query that matches nothing", () => {
    expect(fixtures.search("zzzz-nothing-matches").hits).toEqual([]);
  });

  it("keeps every fixture timestamp parseable", () => {
    for (const run of fixtures.runs().runs) {
      expect(Number.isNaN(Date.parse(run.started_at))).toBe(false);
    }
    for (const document of fixtures.documents().documents) {
      expect(Number.isNaN(Date.parse(document.updated_at))).toBe(false);
    }
  });
});