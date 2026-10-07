# Phase 20 — Execution engine

**Status:** Complete — engine, records and rollback contract built and verified offline
**§64 scope:** Execution engine
**Governing sections:** §21 execution engine, §51 rollback, §28 async job system
(tool-call records), §39 auditability
**Decisions:** [D-022](../../DECISIONS.md) (one writer, bounded attempts, rollback from observed state)
**Commit:** `65fe3c6`

## What this phase delivered

Until now the pipeline stopped at "a human approved this". §21's chain —
approved change → pre-execution validation → marketplace API → response
validation → post-execution verification → audit log — is what this phase
builds, and it is the only code in the application that changes a listing.

- **One writer, and refusals are not rows.** `execute_proposal()` in
  `app/services/execution/` is the sole path that calls a marketplace to change
  something. A proposal that is not approved, has already executed, or targets
  no product raises `ExecutionRefusedError` *before* a job exists: nothing was
  attempted, and a job row would record work that never began. Everything that
  fails after dispatch is a row, because §21 asks that the error be preserved,
  the failure recorded, the affected change identified and recovery given — a
  row is the only shape that survives the process exiting.
- **Verification is a second read, not the write's return.** After
  `update_product`, the engine calls `get_product` again and compares. A
  marketplace that accepted the write and then normalised it is caught here;
  the mismatch is retried inside the same attempt budget (an eventually
  consistent marketplace settles) and reported when it does not.
- **Attempts are bounded by the database, not by hope.** `attempt_count <=
  max_attempts` is a `CHECK` on `execution_jobs`, so no code path — including
  this one — can loop. Only errors marked `retryable` (rate limits,
  transients) are retried; rejected credentials and unknown ids stop after one
  attempt, because the answer will not change.
- **Rollback is automatic when the adapter supports it, from the state
  observed before our first write.** §51 forbids promising universal rollback,
  so `rollback_supported` comes from the adapter, `rollback_reference` and
  `previous_state` are written before the write, and a failure with no rollback
  says in plain words that the marketplace may still hold the change and that
  restoring it is manual. A rollback that itself fails lands at
  `PARTIALLY_FAILED` rather than pretending the state is known.
- **Recovery instructions are written per outcome.** Validation refusal,
  credentials rejected mid-flight, budget exhausted, restored, and "rollback
  failed too" each get their own text, each repeating the attempt budget —
  §21's "provide recovery instructions" is not a generic apology.
- **Every outcome writes an `AuditLog`**, including the refusals that happened
  after dispatch (a failed pre-execution check): who, which job, old state,
  new state, the evidence behind the change, its risk, and the approval it
  executed under, with the recomputable hash §39 already provides.
- **`tool_calls` became a record.** §23's logging requirement was a log line
  Phase 15 emitted; it is now a row too. `ToolRegistry.execute` — the single
  dispatch point every tool call goes through — writes one `tool_calls` row per
  invocation through an optional recorder, with arguments masked (D-010) and
  the result stored as metadata rather than content. A registry built without
  a recorder writes nothing, which keeps Phase 14's "nothing in the graph
  writes to the database" true for callers that have not asked for records.
  `start_agent_run`/`finish_agent_run` open and close the run those rows point
  at, since `tool_calls.agent_run_id` is not nullable.

## Where it lives

- `app/services/execution/engine.py` — `execute_proposal`,
  `ExecutionRefusedError`, pre-execution checks, recovery text
- `app/services/execution/__init__.py` — the package's exports
- `app/services/reconciliation/repositories.py` — `ExecutionJobRepository`
- `app/agents/tools/recording.py` — `DatabaseToolRecorder`, `start_agent_run`,
  `finish_agent_run`
- `app/agents/tools/spec.py` — `ToolCallRecorder` protocol, the recording
  wrapper around `ToolRegistry.execute`
- `tests/unit/fakes.py` — the shared fake session (extracted from Phase 18, so
  both suites answer queries the same way)
- `tests/unit/test_execution_engine.py` — 15 tests
- `tests/unit/test_tool_recording.py` — 9 tests

## Verification

| Check | Result |
|---|---|
| `pytest` | **953 passed, 3 skipped** (910 unit, 43 agent offline, 3 live opt-in) |
| `ruff check app tests alembic scripts` | clean |
| `mypy app` | clean, 108 files |

## Exit criteria

| Criterion | Status |
|---|---|
| Only approved operations execute, each with post-execution validation (§21) | **Met** — unapproved/already-executed/no-product proposals are refused before a job exists (4 tests); the success path proves read-write-read with `verified_value` recorded, and a marketplace that stores a different value is detected and rolled back |
| Rollback is designed before the first live write (§51) | **Met** — capability flag, `previous_state` captured before the write, automatic restore when supported, `PARTIALLY_FAILED` when restore fails, and explicit "no rollback; do it manually" text when not — all asserted |
| `execution_jobs` and `tool_calls` move from schema to record | **Met** — the engine is the first writer of `execution_jobs` (statuses, attempts, error, recovery, response), and `ToolRegistry.execute` writes one `tool_calls` row per invocation including failures, with a test that the default writes none |

## Gaps and honest limits

- **No marketplace has been written to.** Everything above runs against
  `MockConnector` and a fake session. Rate limits, auth expiry, pagination and
  partial failures are the mock's staged errors, not a marketplace's real
  behaviour — the same D-005 gap Phase 19 recorded, now attached to a second
  component.
- **Nothing dispatches execution.** There is no HTTP route, no worker and no
  scheduled job that calls `execute_proposal`; a caller must import it. That
  is deliberate in part: the capability matrix has no `execute` capability
  yet, and deciding who may press that button is a decision, not a default.
- **One operation: `update_product`.** Proposals change a field on an existing
  product, so that is what the engine does. `create_product` and
  `delete_product` exist on the seam and are unreachable from a proposal,
  because no approval flow produces one.
- **The target id is derived, not stored.** `marketplace_products.external_
  product_id` is looked up on each dispatch. A job row therefore records the
  response but not the id it targeted; it is reproducible from the proposal,
  and adding a column that could drift seemed worse than one extra query.
- **`execution_results` (§25's table) does not exist.** Results live on
  `execution_jobs` — `marketplace_response`, `error_message`,
  `recovery_instructions` — so a per-step result table is unnecessary for one
  operation and has not been created for the sake of a list.
- **Tool-call records are written only when a recorder is attached.** The
  agent graph has no HTTP entry point (`run_graph` is called from tests), so
  no `agent_runs` or `tool_calls` row exists in production today. The seam is
  complete and tested; the caller is not built.
- **Batch execution (§28) is not wired to a queue.** One proposal executes per
  call. Draining an approved backlog through Dramatiq is a worker's job and
  this phase did not add one, so "batch execution" remains unimplemented.
