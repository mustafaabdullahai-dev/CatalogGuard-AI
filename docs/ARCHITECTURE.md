# Architecture

CatalogGuard AI — catalog reconciliation and change-safety platform.

---

## The core principle

The LLM never writes to production catalog data. Not directly, not indirectly,
not through a tool call. The pipeline is deliberately split so that reasoning
and authority never sit in the same place.

```
User request
   ↓
Orchestrator (LangGraph)
   ↓
Data retrieval
   ↓
Normalization
   ↓
Reconciliation
   ↓
LLM reasoning ── proposes, with evidence and confidence
   ↓
Structured proposal (validated schema)
   ↓
Deterministic validation ── decides whether it is technically valid
   ↓
Risk engine ── scores consequences
   ↓
Sandbox simulation ── predicts downstream impact
   ↓
Human approval ── controls production
   ↓
Execution engine ── performs approved operations
   ↓
Post-execution validation
   ↓
Audit log
```

Three separations carry the whole design:

1. **Proposing vs. deciding.** The model produces a candidate. Code decides
   whether it is admissible. A high-confidence model output that fails schema
   validation is still rejected.
2. **Authority vs. action.** Who is right (D-002 precedence matrix) is
   independent of whether we may act (risk bands).
3. **Reasoning vs. judgment.** Risk, relationships, and validation are
   deterministic services. The model explains consequences; it does not assign
   final risk.

---

## Why the LLM is not trusted with structured output

Qroq is OpenAI-API-*compatible*, not OpenAI. Its strict JSON-schema structured
output is materially weaker. Since the entire proposal pipeline depends on
schema-enforced output, this is treated as an architectural constraint:

1. Every model call declares a Pydantic schema and validates before use.
2. Validation failure triggers a bounded repair loop.
3. After the repair budget is exhausted, the task fails loudly. It never falls
   through.
4. Prompts are schema-first, since provider-side enforcement cannot be trusted.
5. Retry tokens are counted against the cost budget.

The benefit of this design is that a provider swap later is a change to one
adapter, not to every node.

---

## Source authority (D-002)

Authority is resolved **per attribute**, never system-wide, and never by agent
negotiation. Letting models arbitrate authority produces confidently wrong
results and breaks referential integrity.

| Attribute | Level 1 | Level 2 | Fallback |
|---|---|---|---|
| Inventory | WMS | ERP / PIM | Lowest value (prevents overselling) |
| Pricing | ERP ledger | Shopify | Halt if margin floor breached |
| Titles / SEO | PIM | Content sheet | Overwrite losing sources |
| Shipping address | Courier API | CRM | Escalate on postcode mismatch |

Risk bands gate whether resolution may be applied automatically:

| Band | Behaviour |
|---|---|
| LOW | Auto-resolve, log to evidence ledger |
| MEDIUM | Conditional auto-resolve within margin deviation, notify |
| HIGH | Freeze, route to a data steward |
| CRITICAL | Explicit approval, no automation |

**One rule is deliberately stricter than the attribute's nominal band:** any
resolution that fans out to downstream channels after a human confirms a winner
is treated as HIGH regardless. A single accepted conflict propagating to every
connected channel is the highest blast-radius operation in the product.

---

## Tenancy (D-011)

`Organization → Workspace → Users → Catalogs → Connections`. Every query is
workspace-scoped. Cross-tenant reads are treated as a security defect, not a
bug.

The unit of isolation is the **workspace**, not the organization: one
organization may run separate marketplaces and credential sets per region, so
those are separate tenants with separate approvals and separate risk
configuration.

Isolation is enforced at three layers, because each covers cases the others
miss.

**1. Schema.** Tenant references are composite:

```sql
FOREIGN KEY (workspace_id, catalog_id) REFERENCES catalogs (workspace_id, id)
UNIQUE (workspace_id, id)   -- on every referenced tenant table
```

A row cannot point at another tenant's parent row even if the application
constructs the insert itself. The single-column `catalog_id → catalogs.id` form
is deliberately absent: it would bypass the pairing and make the ORM
relationship ambiguous.

**2. ORM.** `TenantRepository` takes a `TenantContext` in its constructor and
refuses to exist without one. Tenant and soft-delete predicates are applied
centrally, and `workspace_id` is stripped from update payloads so a caller
cannot move a row between tenants.

**3. Database.** Row-level security, `FORCE`d, on all 26 tenant tables:

```sql
CREATE POLICY tenant_isolation ON products FOR ALL
USING      (workspace_id = NULLIF(current_setting('app.current_workspace', true), '')::uuid)
WITH CHECK (workspace_id = NULLIF(current_setting('app.current_workspace', true), '')::uuid)
```

Three details are load-bearing:

- **It fails closed.** An unset or blank setting compares against NULL and
  matches nothing, so an unbound connection sees zero rows, not all rows.
- **`FORCE` is mandatory.** PostgreSQL exempts a table's owner from its own
  policies, and the application connects as that owner. Without `FORCE` the
  policies would exist in the schema and be inert in production.
- **`WITH CHECK` is mandatory.** Read-side protection alone still allows
  inserting rows attributed to another workspace.

The application binds a tenant per transaction with
`set_config('app.current_workspace', <uuid>, true)`. The third argument is
`SET LOCAL`, which discards the value at commit or rollback — required under a
transaction-mode pooler, where a plain `SET` would survive on the backend
connection and leak into the next request.

**One table is deliberately wider than the rest** (D-011, amended in Phase 24):

```sql
-- workspace_memberships only
USING      (user_id = current_user OR workspace_id = current_workspace)
WITH CHECK (workspace_id = current_workspace)
```

Session resolution has to read a user's memberships to learn which workspaces
they belong to, so nothing can bind a workspace before that read. Without this
exception the policy returned nothing, and every signed-in user resolved to
zero workspaces. The widened predicate is on the read side only, and the user
id it compares against is bound by `get_auth_context` from the session it has
already verified — never from a request. `WITH CHECK` is unchanged, so
self-read cannot become self-enrolment.

`organizations`, `users`, and `workspaces` carry no policy: they sit at or above
the boundary, and a policy keyed on a workspace's own `workspace_id` would be
circular. That exemption list is explicit, so a newly added table is a
deliberate act rather than an omission.

`workspaces` therefore has no `workspace_id` column of its own — it *is* the
tenant, and `id` is what every other table points at.

### Immutability and history (D-012, D-013)

| Table | Shape | Reason |
|---|---|---|
| `audit_logs` | `created_at` only; UPDATE and DELETE rejected by trigger | The trail an incident review depends on must not be editable, by any role |
| `product_versions` | `created_at` only | A proposal must be replayable against the exact state it observed |
| `tool_calls`, `llm_usage` | `created_at` only | Per-call records; revision would undermine cost accounting |
| everything else | `updated_at`, `deleted_at` | Soft-deleted so history survives |

Immutable tables have no `updated_at` and no `deleted_at` columns at all, so
"append-only" is a type choice rather than a convention. Retraction is a new
row. `TenantRepository` adapts via `hasattr(model, "deleted_at")`, so both
shapes are read and written through the same repository.

### Migrations are generated

The initial migration is a build artifact produced from ORM metadata, so a
model change without a regeneration fails the test suite. RLS policies are
derived from the metadata at migration time rather than listed by hand, which
means a newly added tenant table is protected by default.

### Authentication (D-014)

```
POST /auth/login ──► throttle check ──► user lookup ──► Argon2id verify
                              │             │                │
                              │             │                └─► rehash if cost changed
                              │             └─► dummy verify if no such user
                              └─► commit counter on failure, always

       success ──► create auth_sessions row ──► Set-Cookie: cg_session (HttpOnly, Secure, Lax)
```

Three properties are worth stating explicitly, because each is a place where the
obvious implementation leaks:

- **Every failure is the same 401.** Wrong password, unknown address, disabled
  account: identical status and body. The reason is logged server-side.
- **Hashing happens regardless.** An unknown address runs a dummy verification so
  it costs the same CPU as a known one. Without it, latency enumerates who has
  an account.
- **Throttle before lookup, and commit on failure.** The login route uses a
  session with no automatic commit and commits explicitly on the failure path;
  a rolled-back counter is a counter that never counts.

Sessions are rows in `auth_sessions`, keyed by the SHA-256 digest of a 256-bit
token. Two expiries: 12 hours idle, 14 days absolute. Both revocation ("log out
everywhere") and scoped revocation ("keep this device") are expressible, which
is the reason for a server-side session over a JWT.

CSRF is a signed double-submit token. The cookie holds `nonce.hmac`, and the
header must carry the identical value. Signing is what stops an attacker who can
plant a cookie — via a subdomain, say — from choosing both halves.

Identity and tenancy are separate dependencies. `get_auth_context` answers "who
is this" and touches only global tables, so `/auth/me` works before a workspace
has been chosen. `get_current_tenant` answers "which workspace, and may they act
there": it checks `X-Workspace-Id` against the caller's memberships, refuses with
403 rather than 404, and binds `app.current_workspace` for RLS before the handler
runs.

There is no HTTP route that creates a user or grants a role.
`python -m app.cli.bootstrap` is the only way in, which is what "no route to an
identity" means in practice.

### Account recovery and invitations (D-017)

```
POST /auth/password-reset         ──► throttle (reset_email, reset_ip)
                                          │
                     known + active ──────┴────── anything else: same 202, same body
                          │
                          └─► retire oldest live token ──► insert digest
                                 ──► queue email_outbox row

POST /auth/password-reset/confirm ──► conditional UPDATE ... WHERE consumed_at IS NULL
                                        AND expires_at > now      ──► one winner
                                          │
                     set hash ──► revoke every session ──► delete remaining tokens
```

The four decisions that matter:

- **The request endpoint cannot tell you whether an address exists.** Same 202,
  same body, same wording, whether the account is real, unknown, or disabled. A
  reset form exists for people who cannot prove who they are; making it an
  existence oracle would leak more than the reset protects. The response says
  the message was *queued*, because queued is what happened.
- **Single-use is enforced by the `WHERE` clause, not by a prior read.** Expired,
  consumed, and revoked tokens all fail as "no row matched". A read-then-write
  would let two concurrent submissions both win.
- **A reset revokes every session and issues none.** A reset is usually a
  response to a compromise; leaving the intruder's cookie alive makes the reset
  cosmetic. Issuing a session from the reset request would authenticate whoever
  read the inbox.
- **Reset throttling is a separate budget from login throttling.** Sharing them
  would let anyone who can submit a reset request for a victim's address keep
  that account locked out of signing in — a denial-of-service aimed at the person
  the feature is meant to help.

Invitations:

```
POST /invitations              (admin only) ──► workspace_invitations row, role bound
POST /invitations/resend       (admin only) ──► same row, new token, old one dead
GET  /invitations              (admin only)
DELETE /invitations/{id}       (admin only) ──► scoped by workspace in the WHERE
POST /auth/invitations/accept  (public)     ──► user + membership, no session
```

- **The role is read from the row.** `accept()` has no role parameter at all, so
  there is nothing for a caller to tamper with. That is what makes it safe to
  send an invitation by email: it grants exactly the capability an admin chose.
- **One live invitation per address per workspace**, enforced by a partial unique
  index. Resending replaces the token on the same row, so revoking is
  unambiguous. Two live rows would mean revoking one and still holding access
  granted by the other.
- **An existing account keeps its password.** Accepting adds the membership and
  leaves the credential alone; supplying a password for an account that already
  has one is an error, not a silent overwrite. An invitation link is therefore
  not a password-reset vector.
- **A weak password leaves the invitation live.** The policy error is raised
  before anything commits, so the same link works on a second attempt. Someone
  who cannot reach the inbox cannot get a replacement link.

**Delivery is a port with one adapter.** There is no mail provider configured, so
`OutboxDelivery` writes the message to `email_outbox` and stops, leaving
`sent_at` NULL: a row that says it was never sent is a queue to drain, whereas a
row marked sent that was never sent is a support ticket about an email that will
never arrive. Messages are written in the same transaction as the token that
caused them, so a crash cannot leave a live token nobody can retrieve. Adding a
provider means adding an adapter and one setting; no caller changes.

Links are never logged, in any environment. D-010 masks any run of 32 or more
characters, which is longer than every token issued here, so a development-only
"log the link anyway" branch would emit a line that looks useful and is not.
`python -m app.cli.outbox` prints queued messages and refuses to run in
production.

### The reconciliation pipeline so far

```
intake row
   │
   ├─► normalization      canonical field names, units, GTIN digits, category paths
   │                      → rules recorded, unmapped columns reported
   │
   ├─► identity           exact GTIN/UPC match?  → link automatically
   │                      variant axis differs? → different product
   │                      otherwise             → proposal for approval
   │
   ├─► authority          per-field rank: PIM/ERP/manufacturer overrides
   │                      tie?                  → conflict, nobody chooses
   │
   ├─► conflicts          §11 content: values, authority, evidence, confidence,
   │                      impact inputs. A conflict is not a proposal.
   │
   ├─► relationships      orphans, cycles, variant axes, duplicate variants,
   │                      unapproved edges. Findings only — no write path.
   │
   ├─► risk               fixed weights, four bands, floors for identity,
   │                      deletion, relationship and compliance consequences
   │
   ├─► simulation         §18 chain on a *copy*: relationships → schema →
   │                      compliance → SEO → conflicts, then the §19 diff,
   │                      the impact report and an explanation. Writes a
   │                      simulation run and the proposal's verdict; never the
   │                      product.
   │
   ├─► approval           §20/§38: a person decides. The risk band fixes how
   │                      many approvers a proposal needs (two for CRITICAL),
   │                      never whether it needs one. Writes the request, the
   │                      action, the proposal's new status and an audit row.
   │
   └─► execution          §21/§51: the only writer of a marketplace listing.
                          Refuses anything unapproved *before* a job exists, then checks,
                          dispatches, re-reads to verify, and rolls back from the state
                          observed before the write. Bounded attempts; one audit row per
                          outcome.
```

The ordering matters and is enforced by types: identity returns a match, not a
product row; conflict detection returns a `DetectedConflict`, not a
`ChangeProposal`; the risk engine takes a `RiskContext` value, not an ORM
object. Each stage can be tested, explained, and replaced on its own.

The simulation takes a value snapshot rather than a session, so what it
predicts is a property of the inputs: same change, same report.

Approval is the one stage with no algorithm in it. Everything before it can
run unattended, and execution is everything after it; the decision itself is
the product's reason for existing (D-020), which is why no risk band, however
low, skips it and why `require_approval_authority` guards every route in front
of it. A stage that
could not run reports `skipped`, which is not `passed` — a missing compliance
corpus must never read as an approval.

Execution is deliberately a single function rather than a stage: only
`execute_proposal()` calls a marketplace to change something, so there is one
place to check approval state, one place to bound attempts, and one place that
can write a listing. Everything a caller can do to it before dispatch — an
unapproved proposal, one already executed, a missing target — is refused
without a job row, because no work was attempted.

Where a value can be wrong in an unrecoverable way, the answer is a proposal.
Where it is merely inconvenient, the answer is a finding. The distinction is the
whole design.

---

## Roles (D-003, D-004)

| Role | Owns | Cannot |
|---|---|---|
| E-commerce Ops Manager | Precedence matrices, risk thresholds, policy | — |
| Data Steward / Catalog Manager | Conflict resolution, approvals | Connectors, ingestion |
| Data / Integration Admin | Ingestion, connectors, source mapping | Approve anything |
| Merchandiser | Inventory and price attributes | Approve |
| SEO / Content Specialist | Titles, descriptions, metadata | Identity, inventory, financial fields |

Credential custody is deliberately separated from approval authority, so
compromising an ingestion account does not grant the right to approve changes.

---

## Async execution

Catalog reconciliation cannot run inside an HTTP request. Dramatiq over Redis
handles ingestion, normalization, reconciliation, embeddings, simulation, and
batch execution.

Constraints: retries are bounded (never unbounded), job state is persisted to
Postgres so in-flight work survives losing Redis, and low free-tier LLM
concurrency means bulk runs queue rather than fail.

---

## Marketplace seam (D-005, Phase 19)

§22's interface exists so the core never learns what a marketplace is called:

```
app/connectors/
   base.py         MarketplaceConnector: §22's nine operations + rollback,
                   data types, and the ConnectorError vocabulary
   mock.py         MockConnector — in-memory, credential-checked, no clock
   credentials.py  encrypt_credentials / decrypt_credentials (Fernet)
   registry.py     CONNECTOR_FACTORIES = {"mock": ...}, connector_for()
```

- **One registry, one entry.** A caller asks for `mock` by the slug stored on
  `marketplace_connections.marketplace`. An unknown name raises rather than
  falling back to the mock, because a fallback would report writes against a
  store that does not exist. No module in the package is named after a
  deferred marketplace; a test asserts it.
- **`retryable` lives on the error, not in the caller.** Rate limits and
  transients say they may repeat; auth, not-found and unsupported say they
  may not. §21's bounded-retry loop reads the class.
- **Rollback is a capability as well as a method.** `supports_rollback=False`
  must raise, which is how §21's "do not pretend rollback is possible" is
  enforceable rather than aspirational.
- **Credentials have one door.** `create_connection` encrypts and inserts;
  `connector_for_connection` decrypts and dispatches. `marketplace_connections`
  has no plaintext credential column, and the test that matters reads the
  table's columns.
- **Its only caller is the execution engine, and that engine has no
  dispatcher.** `execute_proposal` builds a connector per job (D-022); no
  route, worker or scheduler invokes it, and every call in the suite lands on
  `MockConnector` — which is the state D-005 asked for, now attached to a
  second component.

---

## Execution (D-022, Phase 20)

§21's chain in one place — `app/services/execution/engine.py`:

- **Refusals are errors, failures are rows.** Not approved, already executed,
  targets no product → `ExecutionRefusedError` before `execution_jobs` sees
  anything. Everything after dispatch is a row with the error preserved and
  per-outcome recovery instructions, because §21 asks for the failure to be
  recorded and the recovery given, and rows outlive the process.
- **Verify by re-reading.** After `update_product` the engine calls
  `get_product` and compares. The write's own response is a claim by the
  party that made the change; a second read is evidence. A marketplace that
  normalised the value is caught here and rolled back rather than believed.
- **Attempts are a database constraint, not a loop bound.**
  `execution_jobs` has `CHECK (attempt_count <= max_attempts)`, and only
  `ConnectorError.retryable` errors are repeated — rejected credentials stop
  after one attempt, before any write.
- **Rollback starts from data captured before the write.**
  `previous_state` and `rollback_reference` are recorded on attempt 1, so a
  restore never depends on state the write may have destroyed (§51).
  `supports_rollback=False` means the job says so in words and leaves the
  marketplace alone; a failed restore is `PARTIALLY_FAILED`, never `FAILED`
  with an unrecorded mess.
- **One audit row per outcome**, refusals included: who, which job, old and
  new state, evidence, risk, the approval it executed under, and a
  recomputable hash (D-013).
- **Tool calls are rows too.** `ToolRegistry.execute` — the single dispatch
  point — writes one `tool_calls` row per invocation through an optional
  recorder, arguments masked (D-010), results stored as metadata rather than
  content. A registry without a recorder writes nothing, which is how the
  agent graph keeps its "no database writes by default" property.

---

## The console (D-001, D-023, Phase 21)

Six screens, one data rule: **the page says where its numbers came from.**

```
app/
   page.tsx          dashboard    totals read from five list endpoints
   catalog/          explorer     GET /products, GET-form filters, pagination
   approvals/        center       GET /approvals + the four decisions
   reconciliation/   workspace    Source → Truth → Analysis canvas
   sandbox/          sandbox      POST …/simulate, stages + diff + impact
   compliance/       center       GET /compliance/search, cited hits

lib/
   api.ts            server reads: cookies forwarded, workspace from the session,
                     every read returns { data, source, reason }
   writeClient.ts    browser writes: same-origin /api/*, CSRF, refusal wording
   pagination.ts     URL state: page arithmetic, query building, pluralisation
   provenance.ts     the canvas: ranked claims, authority resolution, ties
   diff.ts           the sandbox: diff rows, impact report, stage wording
   notices.ts        what each fallback reason says, in one place
```

- **One origin.** Reads run in server components and forward the cookies the
  browser sent; writes go from the browser through the `/api/*` rewrite. Same
  origin means the session cookie stays on this host and no CORS grant is
  load-bearing (D-014's revocation rests on that).
- **The workspace comes from the session**, never from configuration.
- **Filters are URLs.** No client store for table state; a filtered view is
  shareable, back-button-correct and works without JavaScript.
- **Three rules the screens repeat because the backend owns them:** a proposal
  the sandbox has not validated cannot be approved; a CRITICAL item needs a
  second approver; a stage that did not run is `not run`, never a pass (§18).
- **The risk bands are readable text.** `--band-*` tokens exist because the
  vivid band colours measured 1.9:1–3.1:1 as badge text; the hues are unchanged.

---

## Copilot (D-024, Phase 22)

§31 asks for an assistant that uses tools instead of remembering. The shape that
satisfies it is narrow, and the narrowness is the design:

```
question ──► bounded tool loop (Phase 14's client, llm_max_tool_rounds)
                │
                ├─ product_lookup      product + its conflicts + its proposals
                ├─ conflict_search     disagreements, ranked, with the products
                ├─ change_search       proposals by risk / channel / field / status
                ├─ compliance_search   cited chunks + the signals that ranked them
                ├─ sandbox_simulator   §18's chain on a copy
                └─ Phase 15's four     authority, identity, risk, normalization
                │
                ▼
           CopilotAnswer { answer, citations[], insufficient_data, follow_ups[] }
                │
                ├─ every citation checked against the tools that actually ran
                ├─ tools_used replaced with the registry's execution list
                └─ one agent_runs row, one tool_calls row per call, one audit row
```

- **One capability, one route.** `use_copilot` sits with the ops manager and the
  data steward; the router exposes a single POST that cannot change anything, so
  there is no second gate to drift from the first.
- **Citations are checked, not requested.** A citation naming a tool that never
  ran is dropped; an uncited claim gets one repair with the complaint in the
  prompt; a second failure is a 502. The model's account of its own tool use is
  discarded.
- **Subordinate by construction.** No tool creates, approves or executes. The
  sandbox is the only write, and it writes a simulation — on a copy.
- **Untrusted content stays data** (§34): tool results arrive in
  `<tool_result>` blocks behind the policy preamble, prior turns are labelled as
  context rather than instructions, and the role tells the model to report
  hostile catalog content rather than act on it.

---

## Observability (D-010, D-025, Phase 23)

Catalogs contain proprietary manufacturer files, wholesale margins, and
marketplace listings. Telemetry must not leave our infrastructure.

- Self-hosted Langfuse only. LangSmith and public log aggregators are prohibited.
- Edge masking runs in the logging processor chain, before formatting, so it
  covers traces, errors, and exception payloads — not just log lines.
- Token and cost accounting is built in-house, since there is no vendor
  dashboard.

What Phase 23 added to that list, and what §55 asks for:

```
request ─► RequestContextMiddleware        binds request_id, method, path,
           │                                status, duration_ms; returns
           │                                X-Request-Id; clears afterwards
           ├─► get_current_tenant           binds workspace_id, user_id
           │
           └─► components ─► logger.info    every line inherits the request

observation ─► emit(kind, name, counts) ─► TraceSink
                                             ├── NullTraceSink     (default)
                                             └── LangfuseTraceSink (self-hosted)

GET /api/v1/observability/summary  ──►  rows the system already wrote
                                         (admin capability, read-only)
```

- **Correlation before dashboards.** Most of §32's value comes from two
  absences being fixed: a `bind_context` helper nothing called, and four Langfuse
  settings nothing read.
- **A port, because the destination does not exist until Phase 28.** The adapter
  is written; setting `LANGFUSE_*` is configuration, not a build.
- **Counts, never content.** Events carry durations, statuses, ids and small
  numeric attributes; content-shaped keys are masked at the event boundary too,
  one layer above the log rule they were written for.
- **A sink cannot fail a request**, and plaintext to a remote host is refused.
- **`null` is not `0`.** Retrieval and model-usage signals report `null` until a
  sink exists; `unavailable` names the gap in words.

---

## Testing (D-026, Phase 24)

```
tests/unit          no database, fast, the default shape of the codebase
tests/agent         graph, tools, injection, incomplete data, hallucinations
tests/integration   real pgvector 0.8.6 in a container, or a URL you supply
tests/e2e           empty — Phase 25
```

- **A real engine, not a described one.** The integration suite starts
  `pgvector/pgvector:pg16`, migrates it, and runs as a `NOSUPERUSER` role so
  RLS is enforced rather than bypassed. `CATALOGGUARD_TEST_DATABASE_URL` swaps
  the managed container for one the environment provides, which is how CI runs.
- **The contract is the served document.** 15 tests assert `app.openapi()`:
  every route under the prefix, schemas on responses, a documented failure, the
  tenant header, and authentication declared as the session cookie. FastAPI
  cannot infer a session cookie read by a dependency, so the spec says it
  explicitly.
- **It found three things no unit test could.** An HNSW index missing its
  operator class (migration never ran), RLS never enabled anywhere
  (`enable_rls_sql` joined two statements with a semicolon), and a login flow
  where every user resolved to zero workspaces (`workspace_memberships` is
  tenant-scoped, but reading it is what names the workspace). All three are
  facts about strings reaching a parser and policies meeting queries — exactly
  what a mock cannot show.
- **CI runs the gates on every push**, including a parity check that regenerates
  the migration and fails if it differs.

---

## Known limitations

- **Untested against the hosted database.** Phase 24 ran everything above
  against an ephemeral `pgvector/pgvector:pg16` container: the migration, RLS,
  auth, ingestion and retrieval are all verified against a real PostgreSQL
  16.15 with pgvector 0.8.6. What that does *not* prove is that Neon exposes
  pgvector, accepts these settings, or behaves the same under its pooled
  endpoint. That needs a live authenticated session.
- **No RLS bypass role.** Cross-tenant migration and admin tooling must bind a
  tenant explicitly today. A separately privileged role is a deliberate future
  decision, not an oversight.
- **No embedding-based identity matching yet.** Scoring is token overlap plus
  character ratio: explainable and reproducible, but blind to a paraphrase with
  no shared vocabulary. pgvector is in the schema for the ambiguous band.
- **The sandbox's compliance stage is skipped, and its route has no database
  behind it.** No `ComplianceCheck` implementation exists, so §18's compliance
  step reports that the corpus is unreachable and cannot fail a proposal; and
  `_snapshot_for()`'s queries and the row insert have only ever run against a
  fake session, because D-006 still records the database as unconfirmed. The
  diff also compares against the catalog's current value rather than the
  baseline the proposal was written against, so a stale proposal is not
  rejected for being stale.
- **The marketplace seam has no traffic and no live adapter.** Every
  operation, refusal and credential path is exercised by tests against
  `MockConnector`, and nothing in production dispatches to it: no route
  creates a `MarketplaceConnection`, no route or worker calls
  `execute_proposal`, and only `update_product` is reachable from a proposal
  at all. Real rate limits, auth expiry, pagination and partial failures are
  unvalidated, which is the gap D-005 accepted and Phase 19 restated in the
  terms of what was actually built.
- **The approval center needs a person to be watching.** The queue is
  pull-based: whoever with `approve` capability looks decides, nothing
  notifies anyone, and no approver can be assigned to a proposal beyond
  whoever acts first. There is also no configurable automation policy for
  low-risk proposals — §38 offers one as an option and D-020 declined it for
  the MVP, so "approve all low-risk" is a bulk human action instead.
- **The audit hash is content, not a chain.** `audit_logs.payload_hash` is
  recomputable from the row's own fields, so an edit to a row is visible to
  anyone who checks; but the table has no previous-hash column, so a rewrite of
  a row and its hash together is not. See `docs/phases/phase-18-approval-workflow.md`
  for the rest of that phase's gaps.
- **Risk weights are opinionated and not cross-interacting.** Weights encode a
  view about which changes are costly (D-016). Interaction terms would compound
  scores in ways a reviewer cannot check. §16's thirteen factors were audited
  against the table in Phase 16: all are scored, two of them deliberately
  through shared codes, and the seven weights D-016 added beyond §16 are listed
  in `DECISIONS.md`.
- **Password reset and invitations exist, but nothing is sent.** Both queue to
  `email_outbox` (D-017) because no mail provider is configured. The token
  lifecycle, single-use enforcement, throttling, and authorization are
  implemented and tested; the transport is one adapter away. An operator reads
  the link with `python -m app.cli.outbox`, which is a development affordance and
  refuses to run in production.
- **Password reset is not a delivery guarantee.** A queued message nobody reads
  means a reset that never arrives, and the person cannot tell that from an
  address that does not exist — which is the point of the identical response, and
  also its cost.
- **No MFA or SSO.** The natural next additions; both attach to the session
  record rather than to the password path.
- **Every authenticated request does two indexed lookups** — session, then
  memberships. Caching either re-introduces the revocation problem D-014 was
  chosen to solve.
- **Authentication is built, but never against real SQL.** There is still no
  live database, so the sign-in path, the membership lookup and the
  `app.current_workspace` binding that satisfies RLS have never executed
  against Postgres. The password hashing, session arithmetic, CSRF middleware
  and authorization matrix are covered; the queries are not. Until that first
  real session, RLS denies every tenant query — which fails closed, as
  intended, rather than open.
- **The venv is Python 3.12.14, not 3.13.** `requires-python` is
  `>=3.12,<3.14` and mypy targets 3.12, so both are supported and only 3.12
  has ever been run.
- **Compliance RAG is inside the MVP even though §71 lists it post-MVP.** The
  scope conflict is written up on the phase-13 page. Retrieval is keyword-only
  until an embedding provider exists, and D-005 still rules out a rule-cited
  compliance *decision* in the MVP: the UI must label findings as retrieved
  evidence rather than as authoritative verdicts.
- **The agent graph has four read-only tools and no HTTP surface.** Phase 14
  delivers the orchestration and Phase 15 the tool layer (`app/agents/tools/`),
  so the graph can call deterministic services — but `run_graph` is still only
  called by tests, nothing streams per-node progress to a client, no proposal
  it produces is persisted anywhere, and the lookups that would need a database
  have no tool because D-006 still records the database as unconfirmed.
  Phase 20 added the `agent_runs`/`tool_calls` recording seam, so those rows
  appear the moment a caller attaches a recorder — and no caller has.
- **Execution is mock-only and undispatched.** The engine's verification,
  retry, rollback and recovery text are proven against `MockConnector` and a
  fake session; no marketplace has been written to, no HTTP route or worker
  calls it, and the capability matrix has no `execute` capability — choosing
  who may press that button is a decision, not a default.
- **Dramatiq has no delayed-job scheduling.** Scheduled compliance scans will
  need APScheduler or cron-driven enqueue.
- **The console has never talked to the real backend.** Phase 21 drove all six
  screens in a browser against a throwaway stub that answered the same routes
  with the same field names, because D-006 still records the database as
  unconfirmed. The frontend's contract is verified; the backend's behaviour on
  these pages is not, and the stub is not in the repository.
- **Three §30 pages are missing** — Product Detail, Marketplace Connections,
  Agent Activity — because no route serves a product, a connection or an agent
  run. The dashboard shows what it can and names the gap instead of linking to
  pages that could only render invented data.
- **The catalog's text filter is page-local.** `GET /products` accepts `limit`
  and `offset` only, so a filter narrows the loaded page and the UI says so.
- **No screen can start an execution.** The execution engine (D-022) has no
  route of its own and the capability matrix has no `execute` capability, so
  approving a change decides it and stops there.
- **The copilot has never called a model.** The Qroq key is the shared plaintext
  one awaiting rotation, so Phase 22's turns are tested against a scripted
  client. Its rules — citations, refusals, budgets — are proven; its latency,
  token cost and the provider's actual behaviour are not.
- **The copilot's rate limit is per process.** Several workers means several
  budgets. A shared counter belongs on Redis, whose credentials are unrotated
  (D-024).
- **Six of §23's eighteen tools exist**, so questions needing the other twelve
  get "I cannot see that" rather than an approximation.
- **No trace has ever left the process.** The Langfuse adapter is written and
  env-gated; no instance has been reachable and the deployment target arrives in
  Phase 28, so two of §32's nine signals report `null` and D-010's interim clause
  ("structured logging carries observability until Langfuse is stood up") is the
  current state rather than a plan.
- **The observability summary is capped at 2,000 rows per section** and says so
  when it truncates. There is no aggregation table, no Prometheus endpoint, no
  dashboard and no alerting — tracking is Phase 23's scope; charting and paging
  belong to deployment.