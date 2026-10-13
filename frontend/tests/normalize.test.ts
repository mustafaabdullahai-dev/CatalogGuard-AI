import { describe, expect, it } from "vitest";

import {
  conflictsPage,
  documentsPage,
  productsPage,
  proposalsPage,
  runsPage,
  source,
  sourcesPage,
  toPage,
} from "@/lib/normalize";

/**
 * The API/console contract, asserted from the API's side.
 *
 * Phase 25 drove the console against the real API and `/reconciliation` crashed
 * the server: `plural(undefined, ...)` reaches `count.toLocaleString()`. The
 * fixtures had always carried the envelope the console expects, so the fixtures
 * agreed with the console and both disagreed with the backend — and each half
 * was tested while the seam was not.
 *
 * These payloads are copied from live responses (`curl` against the running
 * API), not invented, so they change when the contract does.
 */

describe("a list endpoint's real response", () => {
  it("has no envelope, which is what crashed the page", () => {
    // The API sends `{sources: [...]}` and nothing else. The console reads
    // `page.total`, and `plural()` calls `count.toLocaleString()`.
    const page = toPage({ sources: [{ id: "a" }, { id: "b" }] }, "sources");

    expect(page.total).toBe(2);
  });

  it("falls back to the row count rather than leaving total undefined", () => {
    const page = toPage({ products: [{ id: "p1" }] }, "products", { limit: 50, offset: 0 });

    // `undefined` here is precisely what made `plural()` throw.
    expect(page.total).toBe(1);
    expect(page.limit).toBe(50);
    expect(page.offset).toBe(0);
  });

  it("keeps an envelope the API does send", () => {
    // GET /api/v1/approvals does send a real envelope.
    const page = toPage(
      { items: [{ id: "a1" }], total: 12, limit: 1, offset: 0 },
      "items",
      { limit: 1, offset: 0 },
    );

    expect(page).toMatchObject({ total: 12, limit: 1, offset: 0 });
  });

  it("survives a response that is not the expected shape", () => {
    const page = toPage("sources", "sources");

    expect(page.items).toEqual([]);
    expect(page.total).toBe(0);
  });

  it("survives null and undefined payloads", () => {
    expect(toPage(null, "sources").items).toEqual([]);
    expect(toPage(undefined, "sources").total).toBe(0);
  });
});

describe("the products endpoint", () => {
  // Copied from GET /api/v1/products.
  const payload = {
    products: [
      {
        id: "d21e92e4-67d3-47bf-8747-7c713d3ec22c",
        catalog_id: "53e99eae-b990-4526-b678-ded1bc09486e",
        sku: "NW-1001",
        brand: "Nordvik",
        title: "Alpine Shell Jacket",
        category_path: "outerwear",
        status: "active",
        version: 1,
      },
    ],
  };

  it("carries the rows under both the page's alias and items", () => {
    const page = productsPage<Record<string, unknown>>(
      payload,
      { limit: 50, offset: 0 },
      "products",
    );

    expect(page.items).toHaveLength(1);
    expect(page.products).toHaveLength(1);
    // Rows pass through untouched: the console's row types are the API's
    // own field names, so renaming them here would be a second vocabulary.
    expect(page.items[0].sku).toBe("NW-1001");
    expect(page.items[0].category_path).toBe("outerwear");
  });
});

describe("the conflicts and proposals endpoints", () => {
  it("wraps conflicts with their envelope", () => {
    const page = conflictsPage<{ id: string }>(
      { conflicts: [{ id: "a" }, { id: "b" }] },
      { limit: 50, offset: 0 },
      "conflicts",
    );

    expect(page.total).toBe(2);
    expect(page.conflicts.map((c) => c.id)).toEqual(["a", "b"]);
  });

  it("wraps proposals with their envelope", () => {
    const page = proposalsPage<{ change_ref: string }>(
      { proposals: [{ change_ref: "CHG-81B32E5C-0001" }] },
      { limit: 50, offset: 0 },
      "proposals",
    );

    expect(page.total).toBe(1);
    expect(page.proposals[0].change_ref).toBe("CHG-81B32E5C-0001");
  });

  it("wraps runs and documents, which the dashboard also reads", () => {
    expect(runsPage({ runs: [{ id: "r1" }] }, "runs").total).toBe(1);
    expect(documentsPage({ documents: [{ id: "d1" }] }, "documents").documents).toHaveLength(1);
  });
});

describe("a source row", () => {
  // Copied from GET /api/v1/catalog-sources. Note what is absent: `status`.
  const raw = {
    id: "a31d7a88-3e89-4d1b-b821-368b5085d834",
    name: "Northwind own catalogue",
    source_type: "internal",
    created_at: "2026-10-13T06:09:48.903320Z",
    last_sequence_timestamp: "2026-10-13T06:09:49.031616Z",
    record_count: 3,
  };

  it("maps source_type onto the field the console's badge reads", () => {
    expect(source(raw).kind).toBe("internal");
  });

  it("derives status from the record count rather than inventing one", () => {
    expect(source(raw).status).toBe("active");
    expect(source({ ...raw, record_count: 0 }).status).toBe("idle");
    // The API may omit the count entirely; an absent count is not an error.
    expect(source({ ...raw, record_count: null }).status).toBe("idle");
  });

  it("keeps the fields both sides agree on", () => {
    expect(source(raw)).toMatchObject({
      id: raw.id,
      name: raw.name,
      record_count: 3,
      created_at: raw.created_at,
    });
  });

  it("labels a source of unknown type rather than showing undefined", () => {
    expect(source({ ...raw, source_type: null }).kind).toBe("unknown");
  });

  it("gives the page both the alias and a real total", () => {
    const page = sourcesPage({ sources: [raw, { ...raw, id: "b" }] }, "sources");

    expect(page.total).toBe(2);
    expect(page.sources.every((s) => typeof s.status === "string")).toBe(true);
  });
});