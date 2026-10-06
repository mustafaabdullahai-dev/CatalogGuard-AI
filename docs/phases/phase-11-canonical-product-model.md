# Phase 11 — Canonical product model

**Status:** Complete (pass has never run against a live database)
**§64 scope:** Canonical product model
**Decisions:** [D-019](../../DECISIONS.md) capture, not reconcile
**Commit:** `fe5d087`

## What this phase delivered

The path from a raw `source_records` row to a canonical `products` row — the
model tables existed and nothing could write to them until this phase.

- **A pass that maps, resolves identity, and captures.** Each record goes
  through `normalize_record`, reduces to an identity question, and runs
  `resolve_identity`.
- **Only an exact identifier match auto-links (D-015).** A fuzzy or strong-text
  match still creates a product *and* records an `IDENTITY_MISMATCH` conflict
  carrying its confidence and evidence. A missed merge is a visible duplicate;
  a wrong merge interleaves two products' histories permanently.
- **SKU collisions link and flag** rather than reject: two sources legitimately
  sharing a SKU must map to one product or the insert fails on the workspace
  unique constraint. The link is flagged `sku_collision` for a human.
- **Products are created `active` with a v1 `product_versions` snapshot in the
  same transaction** — status set explicitly rather than read back from a
  column default that would record `null`.
- **A workspace's Default catalog is created idempotently on first use**,
  because `products.catalog_id` is NOT NULL and nothing created a catalog
  before.
- **`source_records.product_id`** records which product a record folded into.
  NULL means *not canonicalized yet*, which is what makes the pass idempotent,
  and it is `ON DELETE SET NULL` so deleted products send records back to
  re-resolution instead of leaving them pointing at nothing.
- **Cross-source field resolution is deliberately deferred** to Phase 12: each
  source is captured exactly as it described itself.

## Where it lives

- `app/services/catalog/` — canonicalization, identity, normalization
- `app/api/v1/catalog.py`, `app/schemas/catalog.py`
- `app/models/catalog.py` (`products`, `product_versions`, `product_identifiers`)

## Verification

`tests/unit/test_canonicalization.py` — **12 tests**, plus the catalog domain
suite it builds on (`test_catalog_domain.py` — 107). The migration was
regenerated for `source_records.product_id` and re-asserted by the parity suite.

## Gaps and honest limits

- **The pass has never run against a live database.** Unit-tested offline only
  (D-019 records this as an accepted trade-off).
- **Blocking uses identity columns and brand only.** Title-only blocking is not
  attempted in SQL; since only exact identifiers can auto-link, extra recall
  could not change the decision.
- **Relationships (parent-child, variant axes, bundles) are validated but not
  populated by this pass.** Structure comes from source data only.
