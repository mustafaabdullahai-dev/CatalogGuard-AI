# Phase 17 — Sandbox simulation

**Status:** Complete — built and verified offline
**§64 scope:** Sandbox simulation
**Governing sections:** §18 sandbox simulation engine, §19 diff view, §51 rollback
**Decisions:** none new
**Commit:** `f6b96d0` (2026-10-07)

## What this phase delivered

A proposal could be written but not predicted: every one of them sat at
`pending`, and nothing had ever said what a change would do to the catalog
before it did it. §18 asks for exactly that — a run over a copy — and §19 asks
that the result be something a human can read.

- **The engine is `app/services/simulation/`.** `simulate()` takes a
  `ProposedChange` and a `CatalogSnapshot` and returns a `SimulationResult`:
  the §18 chain (`relationship_validation → schema_validation →
  compliance_validation → seo_analysis → conflict_detection`), the §19 diff,
  an impact report, and a plain-language explanation. It reads nothing outside
  the snapshot it was handed, so the same inputs always give the same report.
- **The product is never written.** `ProductState` is a frozen dataclass and
  `applied()` returns a second value object, so "the change" exists only in
  the copy the stages look at. The first test in the file asserts the input is
  identical after the run — the phase's central claim, checked rather than
  assumed.
- **A stage that did not run says `skipped`, never `passed`.** Each stage
  carries `passed | failed | flagged | skipped`, and `validation_for()` only
  lets a `failed` stage reject. Compliance is `skipped` today: the corpus lives
  in a database D-006 still records as unconfirmed, and a missing corpus must
  never read as an approval.
- **Only what the change introduced can fail it.** The relationship stage
  compares the tree before and after; a catalog that was already broken is
  counted in the impact report and shown in its before/after summaries, but
  does not sink every proposal that touches it.
- **Schema rules are the product model's own limits**, and nothing more:
  column max lengths, the required `sku`, numeric fields, and 8–14 digit
  identifiers after stripping separators. An attribute has no column, so a
  proposal that changes one skips the schema stage rather than being judged
  against a rule nobody wrote.
- **`persist_simulation` is the only writer of `validation_status`.** The run
  becomes a `simulation_runs` row and the proposal moves to `simulated` or
  `rejected_by_validation` with its errors kept beside it. Nothing defaults a
  proposal to validated.
- **There is a route:** `POST /api/v1/change-proposals/{proposal_id}/simulate`,
  gated on the same `write` capability as the rest of this router. It loads the
  product, its parent, its children and their siblings — the neighborhood a
  field write can disturb, not the whole catalog — and answers with the full
  report (`SimulationResponse`). A proposal with no product, or one whose
  product or catalog is gone, is refused with a 409 rather than simulated
  against a snapshot assembled from nothing.

## Where it lives

- `app/services/simulation/sandbox.py` — `ProductState`, `CatalogSnapshot`,
  `ProposedChange`, `StageOutcome`, `ComplianceCheck` / `NOT_CONFIGURED`,
  `SimulationResult`, `validation_for()`, `simulate()`, the stage helpers and
  the §19 diff
- `app/services/simulation/persistence.py` — `simulation_run_for()` (pure),
  `persist_simulation()` (run row + proposal transition)
- `app/services/reconciliation/repositories.py` — `SimulationRunRepository`
- `app/api/v1/reconciliation.py` — `_snapshot_for()` and the simulate route
- `app/schemas/reconciliation.py` — `SimulationResponse`
- `tests/unit/test_simulation_sandbox.py` — 31 tests across nine classes

## Verification

| Check | Result |
|---|---|
| `pytest` | **864 passed, 3 skipped** (821 unit, 43 agent offline, 3 live opt-in) |
| `ruff check app tests alembic scripts` | clean |
| `mypy app` | clean, 95 files |

Offline by construction: `simulate()` takes no clock, no session and no
network. The persistence tests run `persist_simulation` against a fake session
(the repositories only `add` and `flush`), so what is asserted is which rows
and transitions are written, not whether a database accepts them.

## Exit criteria

| Criterion | Status |
|---|---|
| A proposal's predicted downstream impact is computed deterministically and stored | **Met** — `simulate()` is pure over its inputs; `simulation_run_for()` builds the row; 31 tests |
| `validation_status` moves off `pending` as a result of a run, not by default | **Met** — `persist_simulation()` is the only writer, with a passing and a failing case |
| The diff view (§19) has data to render: before / proposed / impact | **Met** — field rows with `current` / `proposed` / `indicator`, the tree before and after, the stage list, the impact report, and an explanation |

## Gaps and honest limits

- **The route has never run against a database.** The engine and the
  persistence seam are unit tested; `_snapshot_for()`'s queries, the 404/409
  paths and the row insert are not, because D-006 still records the database
  as unconfirmed. Same caveat as every other phase that touches SQL.
- **Compliance is skipped, so it cannot fail anything.** `NOT_CONFIGURED` is
  the default and no `ComplianceCheck` implementation exists; the stage reports
  "the corpus lives in a database this run cannot reach" and the proposal is
  still validated. §13's corpus is the thing that would make this stage real.
- **The diff compares against the catalog, not the proposal's baseline.**
  `current` is read from the snapshot, which is what the change would actually
  overwrite — but a proposal written against an older value is not rejected
  for being stale, and nothing surfaces that the two disagree. The approval UI
  (Phase 18) is where that comparison belongs.
- **The snapshot is a neighborhood, not the catalog.** Only the product, its
  parent, its children and their siblings are loaded, so an effect elsewhere
  in the tree is invisible to this run — the next reconciliation pass reads
  the rest.
- **Schema rules are column limits only.** No workspace-defined validation
  rules exist, so an attribute that should be an enum is checked only for
  being present in the payload.
- **No UI renders the diff yet.** §19's data exists as JSON; showing it to a
  reviewer is Phase 21's work.
