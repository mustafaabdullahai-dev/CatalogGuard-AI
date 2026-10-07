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

**3. Database.** Row-level security, `FORCE`d, on all 25 tenant tables:

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
   └─► approval           §20/§38: a person decides. The risk band fixes how
                          many approvers a proposal needs (two for CRITICAL),
                          never whether it needs one. Writes the request, the
                          action, the proposal's new status and an audit row.
```

The ordering matters and is enforced by types: identity returns a match, not a
product row; conflict detection returns a `DetectedConflict`, not a
`ChangeProposal`; the risk engine takes a `RiskContext` value, not an ORM
object. Each stage can be tested, explained, and replaced on its own.

The simulation takes a value snapshot rather than a session, so what it
predicts is a property of the inputs: same change, same report.

Approval is the one stage with no algorithm in it. Everything before it can
run unattended, and everything after it is execution; the decision itself is
the product's reason for existing (D-020), which is why no risk band, however
low, skips it and why `require_approval_authority` guards every route in front
of it. A stage that
could not run reports `skipped`, which is not `passed` — a missing compliance
corpus must never read as an approval.

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
- **Nothing in production calls it yet.** Phase 20's execution engine is the
  first caller, so the seam is proven and unused — which is the state D-005
  asked for.

---

## Observability and data residency (D-010)

Catalogs contain proprietary manufacturer files, wholesale margins, and
marketplace listings. Telemetry must not leave our infrastructure.

- Self-hosted Langfuse only. LangSmith and public log aggregators are prohibited.
- Edge masking runs in the logging processor chain, before formatting, so it
  covers traces, errors, and exception payloads — not just log lines.
- Token and cost accounting is built in-house, since there is no vendor
  dashboard.

---

## Known limitations

- **Untested against a live database.** The schema, migration, and RLS are
  rendered to SQL in both directions and asserted against the ORM, but no
  authenticated connection has exercised them. RLS behaviour in particular
  (policy denial, `FORCE` under the owning role) needs a real Neon session to
  confirm.
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
  creates a `MarketplaceConnection` and no job calls a connector. Real rate
  limits, auth expiry, pagination and partial failures are unvalidated, which
  is the gap D-005 accepted and Phase 19 restated in the terms of what was
  actually built.
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
- **Execution path is mock-only.** Real marketplace rate limits and
  partial-failure modes remain unvalidated.
- **Dramatiq has no delayed-job scheduling.** Scheduled compliance scans will
  need APScheduler or cron-driven enqueue.