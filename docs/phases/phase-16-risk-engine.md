# Phase 16 — Risk engine

**Status:** Complete — audited against §16 and verified offline
**§64 scope:** Risk engine
**Governing sections:** §16 risk engine, §37 AI confidence
**Decisions:** [D-016](../../DECISIONS.md) fixed weights with floors, amended
2026-10-06
**Commit:** `ec65470` (2026-10-06)

## What this phase delivered

The engine existed — Phase 12 needed a score to write, so it was built early in
`21eee9e`. What had never happened was the check this phase's first exit
criterion asks for: weighing the weights against §16 rather than against D-016.
Running it found one factor the spec asked for and the code did not have.

- **Inventory impact is now scored.** §16 lists it; 21 factor codes did not
  include it. It fires when the changed field is a stock field (`stock`,
  `quantity`, `on_hand`, … — the product model has no inventory column, so a
  payload's stock column is the only signal it can see) or when the caller
  states how many units are affected (`inventory_units_affected`, from 100).
  Both paths produce one code, and the detail says which path it came by.
- **Indexing impact deliberately shares two codes.** A page deindexes by
  changing how it is discovered: its slug, its category, its SEO fields. A
  separate `indexing_impact` weight would charge the same consequence twice —
  the arithmetic a reviewer cannot check. §16 calls its factors *potential*, so
  this is a mapping choice, not a missing feature, and it is written down where
  the weights are.
- **The conformance table is executable.** The §16 → code table lives in
  `risk.py`'s module docstring (where a reviewer reads the weights) and two
  tests hold it honest in both directions: each §16 line must map to a weight
  that a shaped change actually fires, and each of the 22 weights must be
  claimed by §16 or listed as one of D-016's seven extras. A factor added later
  cannot slip past either side, and neither can a docstring row nobody implements.
- **Factors stay beside the score, provably.** The stored score is clamped to
  the column's 0..100 CHECK while the factors are written in full — that
  pairing was inline inside `_persist_proposal` and therefore untested, so it
  is now `risk_assessment_for()`, a pure function, with two tests: one that the
  row keeps every factor and the level, one where the score genuinely exceeds
  100 and the raw total still adds up from what was stored. That is the third
  exit criterion, checked rather than asserted in prose.
- **The risk tool keeps up.** `assess_change_risk` takes
  `inventory_units_affected` (optional, bounded), and its result model fits
  every weighted factor — guarded by a test, because a truncated factor list is
  a total nobody can re-add.
- **D-016 is amended, not contradicted.** The weights remain a published table,
  the score remains a sum, and the amendment records the new code plus which
  seven weights are D-016's own beyond §16.

## Where it lives

- `app/services/reconciliation/risk.py` — §16 conformance table, `WEIGHTS`
  (22 codes), `THRESHOLDS`, `FLOORS`, `assess()`, `context_from_change()`
- `app/services/reconciliation/engine.py` — `risk_assessment_for()` (the
  capped-score / full-factors pairing)
- `app/agents/tools/catalog.py` — `ChangeRiskArgs.inventory_units_affected`,
  factor list bound
- `tests/unit/test_conflicts_and_risk.py` — `TestSection16Conformance` (19
  tests, parametrized over §16's thirteen lines)
- `tests/unit/test_reconciliation_engine.py` — `TestRiskEvidenceIsStored` (2)

## Verification

| Check | Result |
|---|---|
| `pytest` | **833 passed, 3 skipped** (790 unit, 43 agent offline, 3 live opt-in) |
| `ruff check app tests alembic scripts` | clean |
| `mypy app` | clean, 92 files |

Everything in this phase is offline by construction: the engine takes no clock
and makes no network call (D-016 rule 6), so determinism is a property of the
code and its existing purity tests.

## Exit criteria

| Criterion | Status |
|---|---|
| Weights, floors and bands confirmed against §16, not only D-016 | **Met** — audited both directions; inventory impact added |
| Risk level gates approver count, once Phase 18 defines it | **Not yet applicable** — see below |
| Contributing factors stored beside the score, raw total recoverable | Met — `risk_assessment_for` and two tests |

On the middle row: `requires_approval` is already `True` on every proposal
(`_persist_proposal` comments that the risk level decides *how many* approvers
and that this rule lives in Phase 18). No approver-count logic exists yet
because the approval workflow does not; inventing a count here would be a rule
with nothing to enforce it against.

## Gaps and honest limits

- **Inventory impact is scored from a field name or a caller's number.** No
  inventory feed exists, so nothing validates the units a caller passes — an
  under-scorer passes 0 and understates risk, which is D-016's already-accepted
  trade-off for sales figures too.
- **The storage tests construct the row; they do not insert it.** The pairing
  of capped score and full factors is proven offline against the ORM object.
  The migration parity test proves the column constraint; neither proves Neon
  accepts the row — same database caveat as every other phase.
- **Approver-count gating is untested because it is unbuilt** (Phase 18).
- **Weights are still code constants.** D-016 says "tunable per workspace";
  there is no workspace override table, so today the tuning is a code change
  and a review.
