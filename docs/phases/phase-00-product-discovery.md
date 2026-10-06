# Phase 00 — Product discovery

**Status:** Complete
**§64 scope:** Product discovery
**Decisions:** — (the requirements themselves are the artifact)

## What this phase delivered

The product specification, written before any code: `MASTER_PROMPT.md`, 2607
lines. Its discovery output is the positioning and the problem statement —
fragmented, inconsistent, duplicated and structurally broken product catalogs
across multiple commerce channels — plus the channel list the architecture must
be extensible for (Amazon, Shopify, TikTok Shop, WooCommerce, Walmart, eBay,
adapters for the rest).

The two constraints that shaped every later phase come from here:

- **§1 critical working rule** — work interactively, confirm before building.
- **§5 core product principle** — the LLM must never directly modify production
  catalog data. Every later design (proposals, approvals, sandbox, execution)
  descends from this.

## Where it lives

- `MASTER_PROMPT.md` — the specification, including §64's phase order and §65's
  interaction rule.
- `docs/phases/README.md` — this phase log.

## Verification

Not a coded phase. The artifact is text; its test is whether later phases can be
traced back to a numbered section, which is what `DECISIONS.md` does.

## Gaps and honest limits

Discovery was specified by the project owner rather than derived from user
interviews or market research inside this repository. No discovery research
artifact exists here beyond the specification itself.
