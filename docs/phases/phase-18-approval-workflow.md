# Phase 18 — Approval workflow

**Status:** Complete — built and verified offline
**§64 scope:** Approval workflow
**Governing sections:** §15 change proposal engine, §20 approval center,
§38 human-in-the-loop, §39 auditability
**Decisions:** [D-020](../../DECISIONS.md) amended 2026-10-07 (the approver count)
**Commit:** `e11d29b` (2026-10-07)

## What this phase delivered

Until now a proposal could be written, risk-scored and simulated, and nobody
could decide it. §20 asks for a center where a human approves, rejects,
modifies, asks for another look, and does any of that in bulk — and §39 asks
that every one of those decisions leave a record nobody can quietly amend.

- **The workflow is `app/services/approval/`.** `record_decision()` is the one
  path: it gates the proposal, opens (or reuses) an `ApprovalRequest`, records
  an `ApprovalAction`, moves the proposal, and writes the audit row — in that
  order, so a refused decision leaves nothing behind and an audit row always
  has a real action under it.
- **The risk level decides how many approvers, never whether.** D-020's rule,
  now in code: LOW, MEDIUM and HIGH need one approval each, CRITICAL needs two,
  and no level is ever zero (§38's ladder: HIGH is *explicit* approval, not a
  second person; CRITICAL is multi-step). A partially approved proposal stays
  in the queue with its count visible, and moves on only when the count is met.
- **One actor, one decision per request.** The second approval a CRITICAL
  change needs must come from a second person: an actor who has already
  decided is refused, which is what stops one account from supplying both
  votes. The data integration admin is refused further upstream, by the
  capability matrix behind `require_approval_authority` (D-003, D-004) — this
  phase's tests assert that refusal rather than restating it.
- **Four decisions, each with the consequence §20 implies.** *Approve*
  accumulates; *reject* is final and immediate at any count; *modify* rewrites
  the proposal's value, clears its validation and sends it back to the sandbox
  (an edited value has not been simulated, so it may not be approved as it
  stands); *re-analysis* does the same without changing the value. The last
  three all require a written reason (§39's "why?").
- **The two bulk forms, with one honest result per proposal.** `POST
  /approvals` approves or rejects a selection; `POST /approvals/approve-low-risk`
  approves every low-risk proposal in the queue. A refusal inside a batch is
  reported for that item instead of failing the call — "nine approved, one
  already approved" is a thing a person can act on; a 409 for the whole batch
  is not. Low-risk approval stays a human act: D-020 rejected auto-approval,
  and this makes giving that one approval cheap rather than removing it.
- **Evidence is comparable in one response.** `GET /approvals/{proposal_id}`
  returns the proposal, its risk factors, the stored simulation (§19 diff,
  impact report, explanation), every conflicting value the conflict kept, the
  open request and who has already decided — the §20 "compare evidence" view
  as data, with nothing summarized away.
- **Every decision writes an `AuditLog`** (§39): who, what, when, why, the
  evidence, old and new value, risk, and the approval it belongs to. The row
  carries `payload_hash`, a sha256 over its own canonical fields, so a reader
  can recompute it and see an edit — and the first writer of `audit_logs` is
  this phase, in `app/services/audit.py`.

## Where it lives

- `app/services/approval/workflow.py` — `REQUIRED_APPROVALS`,
  `required_approvals_for()`, `approvable_reason()`, `open_request()`,
  `record_decision()`, the refusal exceptions
- `app/services/audit.py` — `audit_log_for()`, `payload_hash()`,
  `recomputed_hash()`
- `app/services/reconciliation/repositories.py` — `ApprovalRequestRepository`,
  `ApprovalActionRepository`, `AuditLogRepository`
- `app/api/v1/approvals.py` — the queue, the detail, four decisions, two bulk
  routes, all behind `require_approval_authority()`
- `app/schemas/approvals.py` — queue / detail / decision / bulk models
- `app/models/reconciliation.py` — `AuditLog` now inherits
  `ImmutableTenantEntity` (its intended base; same columns, so the migration
  was regenerated)
- `tests/unit/test_approval_workflow.py` — 26 tests
- `tests/unit/test_approvals_api.py` — 7 tests

## Verification

| Check | Result |
|---|---|
| `pytest` | **897 passed, 3 skipped** (854 unit, 43 agent offline, 3 live opt-in) |
| `ruff check app tests alembic scripts` | clean |
| `mypy app` | clean, 100 files |

The workflow tests run against a fake session that answers the workflow's own
queries from the rows it was given — which is how "the request you already
opened" and "the decision you already recorded" can be tested without a
database, rather than being asserted in prose. The API tests follow the
compliance router's pattern: real HTTP in-process, session injected per role.

## Exit criteria

| Criterion | Status |
|---|---|
| Risk level decides how many approvers, never whether one exists | **Met** — `REQUIRED_APPROVALS` from §38's ladder, tested for all four levels and for the negative (`no level asks for zero`) |
| The integration admin can never approve (D-004) — enforced, not intended | **Met** — `require_approval_authority` on every route, with a 403 test for `data_integration_admin` (and the other non-approver roles) on both read and decide |
| Every approval writes an audit row (D-013) the database will not let anyone edit | **Met** — one row per decision with a recomputable hash, tested; immutability itself is the migration's `BEFORE UPDATE`/`BEFORE DELETE` triggers, already asserted by `test_migration_parity.py` |

## Gaps and honest limits

- **No configurable automation policy.** §38 describes LOW → auto-approve as
  a *configurable* option; D-020 rejected auto-approval for the MVP, so every
  level needs a human and there is no settings surface to change that. Adding
  one is a policy decision, not a missing function.
- **The audit hash is not a chain.** `audit_logs` has no previous-hash column,
  so `payload_hash` makes a row *recomputable*, not *linked*: an attacker who
  rewrote a row and its hash together would not be caught by this field.
  Detecting that needs a migration and a walker over the sequence.
- **No live database.** Every row this phase writes — request, action, audit —
  has been constructed and asserted against a fake session, never inserted
  into Postgres. The same D-006 caveat as every other phase.
- **A proposal is one request at a time, and requests are not assigned.** The
  queue is pull-based: whoever with `approve` capability looks at it decides.
  Nothing notifies anyone, and §38's "assign an approver" has no row to assign
  to beyond whoever acts first.
- **The bulk routes are bounded, not streamed.** 200 ids per call, 200
  low-risk proposals per call — deliberate for an MVP queue, and a large
  catalog's backlog will need a job rather than a loop.
- **Conflicts are shown, not resolved here.** The detail returns every
  competing value so a human can compare (§20), but closing a conflict remains
  the reconciliation side's concern; `Conflict.resolved_value` is untouched by
  this phase.
