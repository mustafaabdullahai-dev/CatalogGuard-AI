import { describe, expect, it } from "vitest";
import {
  formatCompact,
  formatConfidence,
  formatMoney,
  formatPercent,
  formatRelative,
  humanizeField,
  isRiskLevel,
  requiresApproval,
} from "@/lib/format";

describe("risk bands", () => {
  it("recognizes exactly the four bands the engine emits", () => {
    expect(["LOW", "MEDIUM", "HIGH", "CRITICAL"].every(isRiskLevel)).toBe(true);
    expect(isRiskLevel("CRITICAL")).toBe(true);
    // A typo must not render as a valid band with no color.
    expect(isRiskLevel("critical")).toBe(false);
    expect(isRiskLevel("SEVERE")).toBe(false);
    expect(isRiskLevel("")).toBe(false);
  });

  it("requires approval for the two upper bands", () => {
    // Mirrors RiskLevel.requires_explicit_approval on the backend. If these two
    // drift apart, the UI can show a change as safe while the API blocks it.
    expect(requiresApproval("HIGH")).toBe(true);
    expect(requiresApproval("CRITICAL")).toBe(true);
    expect(requiresApproval("LOW")).toBe(false);
    expect(requiresApproval("MEDIUM")).toBe(false);
  });
});

describe("money", () => {
  it("always shows cents so a price column stays aligned", () => {
    expect(formatMoney(10)).toBe("$10.00");
    expect(formatMoney(1299.5)).toBe("$1,299.50");
  });

  it("respects the currency from the record", () => {
    expect(formatMoney(24.99, "EUR")).toContain("24.99");
    expect(formatMoney(24.99, "EUR")).toContain("€");
  });

  it("handles zero and negatives without collapsing", () => {
    expect(formatMoney(0)).toBe("$0.00");
    expect(formatMoney(-5)).toBe("-$5.00");
  });
});

describe("compact counts", () => {
  it("keeps large numbers short", () => {
    expect(formatCompact(12480)).toBe("12.5K");
    expect(formatCompact(999)).toBe("999");
  });
});

describe("percentages and confidence", () => {
  it("formats a fraction as a percentage", () => {
    expect(formatPercent(0.821)).toBe("82%");
    expect(formatPercent(0.821, 1)).toBe("82.1%");
    expect(formatConfidence(0.5)).toBe("50%");
    expect(formatConfidence(0.945)).toBe("95%");
  });
});

describe("relative time", () => {
  const now = Date.parse("2026-10-02T12:00:00Z");

  it("picks a unit a human would use", () => {
    expect(formatRelative("2026-10-02T11:59:30Z", now)).toBe("30s ago");
    expect(formatRelative("2026-10-02T11:45:00Z", now)).toBe("15m ago");
    expect(formatRelative("2026-10-02T09:00:00Z", now)).toBe("3h ago");
    expect(formatRelative("2026-09-29T12:00:00Z", now)).toBe("3d ago");
  });

  it("shows a dash rather than NaN for a malformed timestamp", () => {
    // A broken timestamp in a status column reads as a rendering bug; a dash
    // reads as "unknown", which is what it is.
    expect(formatRelative("not-a-date", now)).toBe("—");
  });
});

describe("field names", () => {
  it("turns api field names into sentences", () => {
    expect(humanizeField("category_path")).toBe("Category path");
    expect(humanizeField("description")).toBe("Description");
    expect(humanizeField("seo_readiness")).toBe("SEO readiness");
  });

  it("never misspells an acronym", () => {
    // "Gtin" as a column header would make an operator distrust the screen.
    expect(humanizeField("gtin")).toBe("GTIN");
    expect(humanizeField("sku")).toBe("SKU");
    expect(humanizeField("listing_url")).toBe("Listing URL");
  });

  it("returns the input unchanged when there is nothing to clean", () => {
    expect(humanizeField("")).toBe("");
    expect(humanizeField("__")).toBe("__");
  });
});
