import { describe, expect, it } from "vitest";

import {
  displayValue,
  parseDiff,
  parseImpact,
  stageLabel,
  stageTone,
  verdictLabel,
} from "@/lib/diff";

/** The shapes `app/services/simulation/sandbox.py` actually produces. */
const SANDBOX_DIFF = {
  rows: [
    {
      field: "title",
      current: "Navy Ceramic Mug 350ml",
      proposed: "Navy Ceramic Mug 350 ml",
      indicator: "modified",
      risky: true,
      requires_approval: true,
    },
    {
      field: "brand",
      current: "Kettle & Co",
      proposed: "Kettle & Co",
      indicator: "unchanged",
      risky: false,
      requires_approval: false,
    },
    {
      field: "care_instructions",
      current: null,
      proposed: "Dishwasher safe",
      indicator: "added",
      risky: false,
      requires_approval: false,
    },
  ],
  tree: {
    before: { product: "CG-1001", parent: null, children: [] },
    after: { product: "CG-1001", parent: null, children: [] },
  },
};

const SANDBOX_IMPACT = {
  affected_channels: ["amazon", "shopify"],
  affected_products: 1,
  relationship_findings: { before: { orphans: 0, cycles: 0 }, after: { orphans: 0, cycles: 0 } },
  relationship_valid: true,
  conflicts_remaining: 1,
  sources_still_disagreeing: ["supplier_a"],
  risk: { level: "LOW", requires_approval: true },
  recommendation: "ready for review; no stage failed",
};

describe("displayValue", () => {
  it("renders an em dash for absent values rather than 'null'", () => {
    expect(displayValue(null)).toBe("—");
    expect(displayValue(undefined)).toBe("—");
    expect(displayValue("")).toBe("—");
  });

  it("keeps scalars readable and objects as compact JSON", () => {
    expect(displayValue(350)).toBe("350");
    expect(displayValue(false)).toBe("false");
    expect(displayValue({ material: "ceramic" })).toBe('{"material":"ceramic"}');
    expect(displayValue([1, 2])).toBe("[1,2]");
  });
});

describe("parseDiff", () => {
  it("counts the fields that actually changed", () => {
    const view = parseDiff(SANDBOX_DIFF);
    expect(view.changed).toBe(2);
    expect(view.rows.map((row) => row.indicator)).toEqual(["modified", "unchanged", "added"]);
  });

  it("keeps the risky marker on the changed field only", () => {
    const view = parseDiff(SANDBOX_DIFF);
    expect(view.rows.filter((row) => row.risky)).toHaveLength(1);
  });

  it("renders the before/after tree as text", () => {
    expect(parseDiff(SANDBOX_DIFF).tree?.before).toContain("CG-1001");
  });

  it("drops rows it cannot understand instead of rendering them raw", () => {
    const view = parseDiff({ rows: [{ field: "title" }, { noField: true }, "nonsense"] });
    expect(view.rows).toEqual([
      { field: "title", current: "—", proposed: "—", indicator: "unchanged", risky: false, requiresApproval: false },
    ]);
    expect(view.changed).toBe(0);
  });

  it("survives a missing or empty report", () => {
    expect(parseDiff(null).rows).toEqual([]);
    expect(parseDiff({}).rows).toEqual([]);
    expect(parseDiff({ rows: "not a list" }).rows).toEqual([]);
  });
});

describe("parseImpact", () => {
  it("reads the recommendation, risk and disagreement list", () => {
    const view = parseImpact(SANDBOX_IMPACT);
    expect(view.recommendation).toBe("ready for review; no stage failed");
    expect(view.risk).toEqual({ level: "LOW", requiresApproval: true });
    expect(view.disagreeing).toEqual(["supplier_a"]);
    expect(view.affectedChannels).toEqual(["amazon", "shopify"]);
    expect(view.conflictsRemaining).toBe(1);
    expect(view.relationshipValid).toBe(true);
  });

  it("keeps relationship findings before and after separately", () => {
    expect(parseImpact(SANDBOX_IMPACT).relationshipFindings).toEqual({
      before: { orphans: 0, cycles: 0 },
      after: { orphans: 0, cycles: 0 },
    });
  });

  it("distinguishes a stage that did not run from one that passed", () => {
    expect(parseImpact({ relationship_valid: null }).relationshipValid).toBeNull();
    expect(parseImpact({}).relationshipValid).toBeNull();
  });

  it("survives an empty report", () => {
    const view = parseImpact({});
    expect(view.recommendation).toBeNull();
    expect(view.risk).toBeNull();
    expect(view.affectedChannels).toEqual([]);
  });
});

describe("stage and verdict wording", () => {
  it("never calls a skipped stage a pass", () => {
    expect(stageLabel("skipped")).toBe("not run");
    expect(stageLabel("not_configured")).toBe("not configured");
    expect(stageTone("skipped")).toBe("mute");
    expect(stageTone("failed")).toBe("bad");
    expect(stageTone("flagged")).toBe("warn");
    expect(stageTone("passed")).toBe("ok");
  });

  it("renders a null verdict as not run, which is not valid", () => {
    expect(verdictLabel(null)).toEqual({ text: "not run", tone: "mute" });
    expect(verdictLabel(undefined)).toEqual({ text: "not run", tone: "mute" });
    expect(verdictLabel(true)).toEqual({ text: "valid", tone: "ok" });
    expect(verdictLabel(false)).toEqual({ text: "invalid", tone: "bad" });
  });
});