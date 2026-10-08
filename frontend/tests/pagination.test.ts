import { describe, expect, it } from "vitest";

import {
  buildQuery,
  clampPageSize,
  DEFAULT_PAGE_SIZE,
  matchesText,
  pageHref,
  pageWindow,
  plural,
} from "@/lib/pagination";

describe("clampPageSize", () => {
  it("falls back to the default for absent or nonsensical input", () => {
    expect(clampPageSize(undefined)).toBe(DEFAULT_PAGE_SIZE);
    expect(clampPageSize("")).toBe(DEFAULT_PAGE_SIZE);
    expect(clampPageSize("abc")).toBe(DEFAULT_PAGE_SIZE);
    expect(clampPageSize("17")).toBe(DEFAULT_PAGE_SIZE);
  });

  it("accepts the sizes the page offers", () => {
    expect(clampPageSize("25")).toBe(25);
    expect(clampPageSize("200")).toBe(200);
  });
});

describe("pageWindow", () => {
  it("describes the first page", () => {
    expect(pageWindow({ page: "1", limit: 50, total: 340 })).toMatchObject({
      offset: 0,
      from: 1,
      to: 50,
      page: 1,
      pageCount: 7,
      hasPrevious: false,
      hasNext: true,
    });
  });

  it("describes a middle page and the last one", () => {
    expect(pageWindow({ page: "3", limit: 50, total: 340 })).toMatchObject({
      offset: 100,
      from: 101,
      to: 150,
      hasPrevious: true,
      hasNext: true,
    });
    const last = pageWindow({ page: "7", limit: 50, total: 340 });
    expect(last).toMatchObject({ from: 301, to: 340, hasNext: false });
  });

  it("clamps a page past the end instead of rendering an empty grid", () => {
    const clamped = pageWindow({ page: "99", limit: 50, total: 120 });
    expect(clamped.page).toBe(3);
    expect(clamped.from).toBe(101);
    expect(clamped.to).toBe(120);
    expect(clamped.hasNext).toBe(false);
  });

  it("clamps nonsense page numbers to one", () => {
    expect(pageWindow({ page: "0", limit: 50, total: 120 }).page).toBe(1);
    expect(pageWindow({ page: "-3", limit: 50, total: 120 }).page).toBe(1);
    expect(pageWindow({ page: undefined, limit: 50, total: 120 }).page).toBe(1);
  });

  it("reports an empty collection as page 1 of 1 with no rows", () => {
    const empty = pageWindow({ page: "1", limit: 50, total: 0 });
    expect(empty).toMatchObject({ from: 0, to: 0, page: 1, pageCount: 1, hasNext: false });
  });
});

describe("buildQuery", () => {
  it("keeps filters, drops empties, applies overrides", () => {
    expect(buildQuery({ q: "mug", status: "active", page_size: "50" }, { page: 2 })).toBe(
      "?page=2&page_size=50&q=mug&status=active",
    );
  });

  it("removes a filter when the override is empty", () => {
    expect(buildQuery({ q: "mug", status: "active" }, { status: "" })).toBe("?q=mug");
  });

  it("returns an empty string when nothing survives", () => {
    expect(buildQuery({ q: "", status: undefined })).toBe("");
  });

  it("builds a page link that preserves filters and adds only the page", () => {
    expect(pageHref("/catalog", { q: "mug", page_size: "25" }, 3)).toBe("/catalog?page=3&page_size=25&q=mug");
  });
});

describe("matchesText", () => {
  it("matches case-insensitively across fields", () => {
    expect(matchesText(["CG-1001", "Navy Mug", "Kettle"], "mug")).toBe(true);
    expect(matchesText(["CG-1001", "Navy Mug", "Kettle"], "KETTLE")).toBe(true);
    expect(matchesText(["CG-1001", "Navy Mug"], "skillet")).toBe(false);
  });

  it("treats an empty needle as no filter", () => {
    expect(matchesText(["anything"], "   ")).toBe(true);
  });

  it("does not throw on null fields", () => {
    expect(matchesText([null, undefined, "Navy"], "navy")).toBe(true);
  });
});
describe("plural", () => {
  it("agrees with the count", () => {
    expect(plural(1, "conflict")).toBe("1 conflict");
    expect(plural(0, "conflict")).toBe("0 conflicts");
    expect(plural(2, "conflict")).toBe("2 conflicts");
    expect(plural(4820, "conflict")).toBe("4,820 conflicts");
  });

  it("accepts an irregular plural", () => {
    expect(plural(1, "candidate chunk")).toBe("1 candidate chunk");
    expect(plural(3, "candidate chunk")).toBe("3 candidate chunks");
  });
});
