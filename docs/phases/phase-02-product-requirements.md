# Phase 02 — Product requirements

**Status:** Complete
**§64 scope:** Product requirements
**Decisions:** [D-002](../../DECISIONS.md) source authority · [D-003](../../DECISIONS.md)
personas and approval authority · [D-004](../../DECISIONS.md) integration admin ·
[D-005](../../DECISIONS.md) MVP scope

## What this phase delivered

Four decisions that later phases cannot proceed without:

- **D-002 — source authority.** Attribute-level precedence rather than
  whole-source precedence: which source wins *per field*, so a supplier's price
  and the marketplace's title can each be authoritative where they actually are.
  Ties escalate to a human instead of being broken silently.
- **D-003 — personas, roles, approval authority.** The role matrix: ecommerce
  ops manager, data steward, data integration admin, merchandiser, SEO
  specialist, and which capabilities each holds. Approval authority is a
  capability, not a seniority claim.
- **D-004 — data/integration admin.** A role that holds connector credential
  custody and deliberately **cannot approve**. One compromised marketplace
  credential must never be able to approve the listing changes it enables.
- **D-005 — MVP scope.** §71 adopted verbatim: fourteen items, no marketplace
  integrations, connectors deferred until the core model is stable.

## Where it lives

- `DECISIONS.md` — the four entries with options and trade-offs.
- `app/models/tenancy.py` — `RoleCapabilities`, the single matrix D-003/D-004
  are enforced from.

## Verification

Requirements phase. The requirements that became code are verified where the
code is: capability checks in `tests/unit/test_authorization.py` (15 tests) and
`test_access_control.py` (32).

## Gaps and honest limits

- **D-005 conflicts with §64.** D-005 adopted §71 verbatim, and §71's MVP list
  contains no compliance RAG — D-005 even records "No compliance RAG in MVP" as
  an accepted trade-off with the consequence that compliance findings must be
  labelled heuristic. §64 nevertheless schedules **Phase 13 — Compliance RAG**
  in the MVP phase order, and Phase 13 has been built. The conflict is not
  resolved; see [Phase 13](phase-13-compliance-rag.md).
- Source authority reads D-002's defaults; per-workspace overrides
  (`workspace.source_authority_config`) are deliberately unwired.
