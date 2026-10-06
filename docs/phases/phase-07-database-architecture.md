# Phase 07 — Database architecture

**Status:** Complete (offline verified; **never applied to a live database**)
**§64 scope:** Database architecture
**Decisions:** [D-011](../../DECISIONS.md) tenant isolation · [D-012](../../DECISIONS.md)
soft deletion and explicit version history · [D-013](../../DECISIONS.md)
append-only audit logs
**Commit:** `a48a007`

## What this phase delivered

**33 tables** across `tenancy`, `catalog`, `reconciliation`, `platform` and
`compliance`, generated into one migration (`alembic/versions/20261001_0001-*`,
revision `0001`) from `app/models` rather than written by hand.

**Tenant isolation in three layers** (D-011), each covering what the others
miss:

1. **Schema** — composite foreign keys `(workspace_id, parent_id)`, so a row
   cannot reference another tenant's parent row.
2. **ORM** — `TenantRepository` cannot exist without a `TenantContext`, and
   applies the tenant and soft-delete predicates centrally. Its model bound is
   the union of the three tenant-owned bases, so a repository cannot be
   declared for a table that has no `workspace_id`.
3. **Database** — row-level security with `FORCE`, reading the transaction-local
   `app.current_workspace`. RLS denies when no tenant is bound: no tenant means
   no rows, not every row.

**Soft deletion and history (D-012).** `deleted_at` on mutable tenant tables;
immutable tables (sessions, audit rows, identifiers) have no `deleted_at`
column at all, and that absence is the assertion.

**Audit logs are immutable in the database (D-013).** A `BEFORE UPDATE` /
`BEFORE DELETE` trigger raises `restrict_violation` for every role, including
the application.

## Where it lives

- `app/db/base.py`, `app/db/repository.py`, `app/db/rls.py`, `app/db/rows.py`
- `app/models/{tenancy,catalog,reconciliation,platform}.py`
- `alembic/versions/20261001_0001-initial_catalog_schema.py` (generated)

## Verification

| Check | Tests |
|---|---|
| Migration ↔ model parity, index parity, RLS SQL, generated DDL | `test_migration_parity.py` — 163 |
| RLS policy behaviour with a mocked engine | `test_rls.py` — 42 |
| Tenant predicate, cross-tenant refusal | `test_tenant_isolation.py` — 28 |

The migration renders to SQL in both directions and is asserted against ORM
metadata, which catches drift and malformed DDL.

## Gaps and honest limits

- **Neon has never seen this schema.** RLS in particular — policy denial,
  `FORCE` under the owning role, the owning role's bypass behaviour — needs a
  real session. Every table above is *implemented, not verified against
  Postgres*.
- **No RLS bypass role.** Cross-tenant migration and admin tooling must bind a
  tenant explicitly. A privileged role is a deliberate future decision.
- Index access methods on the two retrieval indexes (GIN, HNSW) were generated
  as plain btree until [Phase 13](phase-13-compliance-rag.md) fixed the
  generator — a bug an index-name parity check could not see and Postgres
  would have rejected on apply.
