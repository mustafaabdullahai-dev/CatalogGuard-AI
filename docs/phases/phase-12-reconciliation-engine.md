# Phase 12 — Reconciliation engine

**Status:** Complete
**§64 scope:** Reconciliation engine
**Decisions:** [D-020](../../DECISIONS.md) authority proposes, risk scores, humans decide
**Commit:** `e70f52c`

## What this phase delivered

The first **writer** for the product's central objects. Conflict detection,
source authority and risk scoring had all existed as logic with nothing calling
them; this phase gave them a caller.

- **A run is scoped to one catalog** and records a `ReconciliationRun` bound to
  `catalog_version`, so findings replay against the state they saw (§50).
- **Source authority proposes; it does not decide** (§11). Each conflict records
  the disagreement, and a `ChangeProposal` is written only when authority
  resolved *and* the proposed value differs from the stored one. A tie writes a
  conflict and no proposal.
- **Risk is scored deterministically** by the D-016 engine: fixed weights with
  floors, clamped confidence, contributing factors stored beside the score.
- **Every proposal is `requires_approval=True` → `PENDING_APPROVAL`.** Risk
  level decides *how many* approvers (Phase 18), never *whether* one exists.
- **`validation_status` stays `pending`** until the sandbox (Phase 17) runs.
- **Re-runs supersede.** Open conflicts and proposals for the same
  (product, tracked field) are soft-deleted first; `APPROVED` and beyond are
  never touched, so an approval is never overwritten and the queue does not
  refill itself.

## Where it lives

- `app/services/reconciliation/` — engine, repositories
- `app/services/catalog/{authority,conflicts,risk}.py` — the deterministic trio
- `app/api/v1/reconciliation.py`, `app/schemas/reconciliation.py`
- `app/models/reconciliation.py` (`conflicts`, `change_proposals`,
  `risk_assessments`, `reconciliation_runs`)

## Verification

| Check | Tests |
|---|---|
| Run lifecycle, supersede, API surface | `test_reconciliation_engine.py` — 12 |
| Conflict detection, authority resolution, risk scoring | `test_conflicts_and_risk.py` — 46 |

## Gaps and honest limits

- **No `ApprovalRequest` rows yet.** The proposal carries its risk level; the
  gate that records *who* may approve is Phase 18, so a pending proposal has no
  approver assigned.
- **`validation_status` is `pending` forever** until Phase 17 — nothing
  validates yet.
- **Per-workspace source authority is not wired.** Every workspace gets D-002's
  defaults; `workspace.source_authority_config` is unread.
- **Products are processed in `limit` batches, not streamed**, and
  `build_observations` re-normalizes each record per run — correct, not cached.
  Profiling belongs with Phase 57.
- **No live run.** The pass has never executed against Postgres.
