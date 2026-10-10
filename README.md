# CatalogGuard AI

**Autonomous e-commerce catalog reconciliation and change-safety platform.**

The LLM proposes. Deterministic code validates. Rules constrain. The sandbox
simulates. Humans approve. Connectors execute. Logs remember.

---

## Current status

**Phases 8–21: authentication, catalog domain, conflicts, risk, ingestion, the
canonical model, reconciliation, the compliance corpus, LangGraph orchestration,
AI tools, sandbox simulation, the approval workflow, the marketplace seam, the
execution engine and the console.** Sign-in, tenant isolation, the catalog domain
(normalization, identity, relationships, source authority), conflict detection,
deterministic risk scoring, catalog ingestion (validation, CSV/JSON/Excel parsing,
schema detection and raw storage), canonicalization (raw records → `products`,
identifiers and v1 versions), the reconciliation engine (source authority →
conflicts → risk-scored change proposals), compliance retrieval (chunked
marketplace policy, hybrid keyword + vector search, citable evidence), and the
agent graph (intake → normalize → analyze → finalize, with D-007's schema
validation and bounded repair on every model call), four read-only agent tools
the graph can call through a bounded tool loop (§23), and the sandbox that
predicts a proposal's impact on a copy of the catalog before it is approved
(§18, §19), and the approval center that decides those proposals — approve,
reject, modify, request re-analysis, in bulk or one at a time, with every
decision written to an immutable audit row (§20, §38, §39) — are complete and
tested, as is the execution engine that carries an approved change to a
marketplace, reads it back to prove it landed, and rolls it back when it did
not (§21, §51), with a `tool_calls` record of every tool invocation (§23) —
all of it **1144 backend tests** (1141 passed, 3 opt-in live-provider tests
skipped) — **42 of them against a real PostgreSQL 16.15 with pgvector 0.8.6**
(D-026) — and a **79-test frontend suite**.
The marketplace seam those writes go through exists too: one interface, one
mock adapter, credentials encrypted at rest (§22, §33, D-005), with every live
integration still deferred.

The console is six screens on top of those routes: dashboard, catalog explorer,
approval center (whose four decisions now call the API and write the same audit
rows the backend wrote in Phase 18), the Source → Truth → Analysis
reconciliation canvas with per-attribute provenance, the sandbox with §18's
stages and §19's diff, and compliance search with a citation on every hit. It
forwards the session cookie from the server, sends writes through the same origin
with the CSRF token, states in a banner whenever it is showing fixtures, and
scores **100 for accessibility** in Lighthouse on the pages audited. Three §30
pages — Product Detail, Marketplace Connections, Agent Activity — are not built,
because no API route serves a product, a connection or an agent run; and it has
never run against the real backend, because no database has ever been reachable
(D-006).

The copilot (`POST /api/v1/copilot/query`) answers questions about a workspace
through five read-only tools — product lookup, conflict search, change search,
compliance search with citations, and the sandbox — behind a new `use_copilot`
capability held by the ops manager and the data steward. It cannot create,
approve or execute anything, every claim is checked against the tools that
actually ran, and an answer it cannot cite is a 502 rather than a paragraph
(D-024). It is an API only: no console panel, no streaming, and no model has
ever answered, because the Qroq key is still awaiting rotation.

Every log line is correlatable: each request binds a `request_id`, workspace and
user, returns the id as `X-Request-Id`, and reports its own status and duration
(§55). Observations go through a `TraceSink` port whose default adapter discards
them and whose Langfuse adapter is self-hosted and env-gated, per D-010 — so a
deployment with no configuration exports nothing. `GET
/api/v1/observability/summary` aggregates the rows the system already writes:
latency percentiles, token totals and repair rate, per-tool failures, retrieval
signals and execution outcomes, naming in words anything it cannot see.

A live LLM provider **has** now been called three times: two schema-validated
answers, fast and reasoning tier, plus a real tool call that resolved source
authority for a GTIN. Nothing has run against a live database.
Every phase's delivery, gates and gaps are recorded in
[`docs/phases/`](docs/phases/README.md).

| Component | State |
|---|---|
| Repository structure | Scaffolded |
| Python 3.12 venv (`requires-python >=3.12,<3.14`) + pinned dependencies | Installed |
| Settings, logging, edge masking | Implemented, unit tested |
| FastAPI app shell, `/health`, `/ready`, OpenAPI | Unit tested in-process |
| ORM: 33 tables across tenancy, catalog, reconciliation, platform, compliance | Implemented, unit tested |
| Email/password sign-in, Argon2id, server-side sessions (D-014) | Implemented; **verified against a real database** (Phase 24) — token stored as a digest, revocation is a row, replay refused |
| Password reset: single-use tokens, session revocation, outbox delivery (D-017) | Implemented, unit tested |
| Workspace invitations: role bound at issue, admin-gated (D-017) | Implemented, unit tested |
| Catalog normalization with an auditable alias table (D-015) | Implemented, unit tested |
| Product identity resolution; only exact identifiers auto-link (D-015) | Implemented, unit tested |
| Parent-child validation: orphans, cycles, variant axes, bundles (D-015) | Implemented, unit tested |
| Source authority with per-field overrides; ties escalate (D-015) | Implemented, unit tested |
| Conflict detection with available values, evidence and impact (§11) | Implemented, unit tested |
| Deterministic risk scoring: 22 weights, four bands, five floors, §16 conformance audited (D-016) | Implemented, unit tested |
| Catalog ingestion: validation, CSV/JSON/Excel parsing, schema detection, raw storage (D-018) | Implemented, 44 unit + **4 real-database tests** (multipart upload, 415 on an unsupported file, raw data preserved, cross-tenant 404) |
| Ingestion sync/async seam (Dramatiq worker) | Implemented; **unverified** — no live Redis |
| Canonical product model: raw records → products, identifiers, v1 versions (D-019) | Implemented, 12 tests |
| Catalog anchor (auto Default catalog per workspace) | Implemented, unit tested |
| `source_records.product_id` provenance column (migration regenerated) | Implemented, parity tested |
| Reconciliation engine: source authority → conflicts → risk-scored proposals (D-020) | Implemented, 12 tests |
| Change proposals, risk assessments, runs | Implemented, unit tested |
| Re-run supersede (no duplicate approval queue) | Implemented, unit tested |
| Approval center: queue, four decisions, bulk low-risk, CRITICAL needs two approvers | Implemented, 33 tests |
| Audit row per decision, `payload_hash` recomputable from the row (D-013) | Implemented, unit tested |
| Marketplace seam: `MarketplaceConnector`, mock adapter, encrypted credentials (§22, §33, D-005) | Implemented, 32 tests; **no live adapter** — Shopify/Amazon/TikTok deferred |
| Execution engine: pre-checks, verify-by-reread, bounded attempts, rollback (§21, §51, D-022) | Implemented, 15 tests; **no route or worker calls it**, and only `update_product` is reachable |
| Compliance corpus: section-aware chunking, content-hash dedup (D-021) | Implemented, 52 tests |
| Hybrid retrieval: GIN keyword `ts_rank` + pgvector cosine, deterministic rerank | Implemented; **verified against real vectors** (Phase 24) — ordering, SQL filters, citations, tenant isolation |
| Compliance API: ingest / list / search with per-hit citations and a signal note | Implemented, 12 tests |
| Embeddings: provider port with a null adapter | Implemented; **no embedding provider works** — `/embeddings` answered `model_not_found` for every candidate id (`504009f`), so retrieval runs keyword-only |
| RBAC guards and tenant binding wired to the session | Implemented; exercised through the real dependency chain over HTTP (Phase 24), not by overriding the guard |
| CSRF (signed double-submit) and security headers | Implemented; exercised over real HTTP with `Secure` cookies (Phase 24). Formal security testing is Phase 26 |
| Tenant isolation: composite FKs, tenant-scoped repository, RLS policies (D-011) | Implemented; **verified against a real database as a non-superuser** (Phase 24); policies widened for self-readable memberships, and that narrowing is pinned by three tests |
| Append-only audit logs enforced by trigger (D-013) | Implemented, unit tested |
| Initial migration, generated from ORM metadata | **Applied to real Postgres 16.15 + pgvector 0.8.6**; three migration-time bugs found and fixed (Phase 24) |
| Postgres (Neon) | TLS verified; **not yet applied**, awaiting rotation |
| Integration suite: schema, RLS, repositories, auth, ingestion, retrieval (§69 chain) | **42 tests against a real database** (D-026); RLS asserted as a non-superuser, not bypassed |
| OpenAPI contract: routes, schemas, documented failures, declared authentication (§42) | **15 tests** over the served spec; found 3 routes documenting only success, and an API that declared no authentication at all |
| Agent failure modes: prompt injection, incomplete data, hallucination resistance (§40) | **16 tests**; the framing is asserted, a live model's obedience is not |
| CI: ruff, mypy, both suites, migration parity, frontend gates | `.github/workflows/ci.yml`, every push |
| Redis (Upstash) | **Verified live** — Redis 8.4.0, TLS required |
| LLM (Qroq) | **Wired and live-verified** — fast (`openai/gpt-oss-20b`) and reasoning (`openai/gpt-oss-120b`) confirmed against the endpoint; key still needs rotation |
| Agent graph (§7 roles, §24 state, D-007 validation layer) | Implemented, 12 unit tests + 2 opt-in live tests |
| Agent tools: normalize_field, resolve_source_authority, resolve_identity, assess_change_risk (§23) | Implemented, 30 tests + 1 opt-in live tool-call test; 4 of §23's 18 tools |
| `tool_calls` and `agent_runs` records: one row per dispatch through `ToolRegistry`, arguments masked (§23, D-010) | Implemented, 9 tests; **nothing writes them in production** — no recorder attached to a live caller |
| Sandbox simulation: §18 chain on a copy, §19 diff, `POST /change-proposals/{id}/simulate` | Implemented, 31 tests; compliance stage skipped (no corpus), route untested against a database |
| Console: dashboard, catalog explorer, approval center, reconciliation canvas, sandbox, compliance center | Implemented, 79 frontend tests; six screens driven in a browser (Chrome DevTools), Lighthouse accessibility 100 on three pages; **never run against the real backend** (D-006), and three §30 pages have no route to render |
| Approval decisions from the UI: approve, edit, reject, re-analysis, bulk, approve-all-LOW | Implemented; verified in a browser against a stub API — CSRF token and `X-Workspace-Id` on every write, each decision removed its row from the queue |
| Copilot: five read-only catalog tools, enforced citations, `use_copilot` gate (§31, D-024) | Implemented, 53 tests; **API only** — no console panel, no streaming, and no model has answered yet (Qroq key unrotated); rate limiting is per process |
| Request-scoped log context: `request_id`, workspace, user, status, duration (§55) | Implemented, 20 tests |
| Trace port with null + self-hosted Langfuse adapters; events carry counts, never content (D-010, D-025) | Implemented, 25 tests; **no trace has ever left the process** — no Langfuse instance reachable (Phase 28) |
| `GET /observability/summary`: latency p50/p95, tokens, per-tool failures, retrieval signals, execution outcomes (D-007) | Implemented, 17 tests; gated on `admin`; retrieval and model-usage signals report `null` until a sink is configured |
| Outbound mail transport | Not configured; messages queue to `email_outbox` (D-017) |

Users cannot self-register: the first administrator is created by a CLI, and
every subsequent user arrives through an invitation an administrator issues.
A lost password is resolved with `POST /api/v1/auth/password-reset`. Both were
gaps in D-014 and are closed in D-017.

Neither flow sends mail yet. Both queue a message to `email_outbox` and stop,
because no mail provider is configured. `python -m app.cli.outbox` prints the
queued messages, which is how the flows are exercised end to end offline.

The migration **has** now been applied — to an ephemeral
`pgvector/pgvector:pg16` container, in Phase 24, by the integration suite. That
is why three defects it would have shipped with are fixed: an HNSW index missing
its operator class, row-level security never being enabled, and a login path
that resolved every user to zero workspaces. What remains unverified is Neon
itself: that the hosted provider exposes pgvector and accepts these settings.
Everything else above marked *implemented* is exercised by a real database
unless its row says otherwise.

---

## Blockers

Three items gate the next phase, all requiring action outside the codebase:

Phase 24 removed the fourth: there is no longer an untested migration. What
remains below is what a database, a credential or a browser outside this
repository can only answer.

1. **Rotate the Qroq API key.** It was shared in plaintext — and it has since
   been used for two live provider calls, which proves it works and does not
   make it private. Put the new value in `.env` as `QROQ_API_KEY`. Config
   refuses to start while it is a placeholder.
2. **Rotate the Neon database password.** Also shared in plaintext. Update
   `DATABASE_URL` in `.env`.
3. **Rotate the Upstash Redis password.** Also shared in plaintext. Update
   `REDIS_URL` and `DRAMATIQ_BROKER_URL` in `.env`.

The originally supplied Redis URL was a placeholder that did not resolve in
DNS; a working endpoint was supplied later.

---

## Layout

```
CatalogGuard AI/
├── backend/
│   ├── app/
│   │   ├── agents/       LangGraph orchestration (nodes, state, client, tools)
│   │   ├── api/v1/       FastAPI routers
│   │   ├── core/         config, logging, masking
│   │   ├── db/           engine, session, models
│   │   ├── rag/          compliance retrieval
│   │   ├── services/     deterministic domain logic
│   │   ├── workers/      Dramatiq actors
│   │   └── main.py       ASGI factory
│   ├── alembic/          migrations
│   ├── tests/            unit, agent; integration and e2e are empty
│   └── pyproject.toml
├── frontend/             Next.js app, auth screens and shell
├── docs/
├── scripts/
├── docker/
├── .env.example
└── DECISIONS.md
```

---

## Running locally

```bash
cd backend
source .venv/bin/activate
QROQ_API_KEY=<key> python -m uvicorn app.main:app --reload
```

Swagger UI at `http://localhost:3000/docs` when the frontend proxies, or
`http://localhost:8000/docs` for the API directly.

### Tests and lint

```bash
python -m pytest -q                   # 1141 passed, 3 skipped
python -m pytest -m "not integration" # 1099 passed — no database, no Docker
python -m pytest -m integration tests/integration   # 42 passed, needs Docker or a URL
cd frontend && npm test               # 79 vitest tests (pure modules, fixtures, write client)
CATALOGGUARD_LIVE_LLM=1 python -m pytest tests/agent -q   # opt-in: 3 live LLM calls (2 schemas + 1 tool call)
python -m ruff check app tests alembic scripts
python -m mypy app
```

The integration suite starts its own `pgvector/pgvector:pg16` container, or
uses `CATALOGGUARD_TEST_DATABASE_URL` if you set it — which is what CI does. It
runs as a non-superuser role, because a superuser bypasses RLS and would make
every isolation test meaningless. Without Docker and without that variable it
**skips** with a reason rather than passing against nothing (D-026).

### Migrations

The initial migration is a build artifact, generated from the ORM. Edit
`app/models/`, then regenerate:

```bash
cd backend
python -m scripts.generate_initial_migration   # rewrites alembic/versions/20261001_0001-*
```

A test asserts the committed file equals a fresh generation, so a model change
that was never regenerated fails the suite rather than shipping.

To render the SQL without a database, pass a URL Alembic will not connect to:

```bash
python -m alembic -x url="postgresql://u:p@example.invalid/db" upgrade head --sql
python -m alembic -x url="postgresql://u:p@example.invalid/db" downgrade 0001:base --sql
```

### Tenant isolation

Three independent layers, all required (D-011):

1. **Schema** — composite foreign keys `(workspace_id, parent_id)`, so a row
   cannot reference another tenant's parent row.
2. **ORM** — `TenantRepository` refuses to exist without a `TenantContext` and
   applies the tenant and soft-delete predicates centrally.
3. **Database** — row-level security with `FORCE`, reading the
   transaction-local setting `app.current_workspace`.

RLS denies when no tenant is bound, so a query without one returns nothing
rather than everything. Authentication (Phase 9) is what binds a tenant: the
server checks `X-Workspace-Id` against the caller's memberships and sets
`app.current_workspace` for the transaction. That binding has been unit-tested
but has never run against a real database, so today every tenant query still
fails closed — by absence of a database rather than by absence of identity.

```python
from app.db.session import tenant_scope

async with tenant_scope(workspace_id) as session:  # bound for this transaction
    ...
```

### Authentication (D-014)

Email and password against an Argon2id hash, exchanged for an opaque session
cookie. The session lives in Postgres so it can be revoked; the cookie carries
only a random token, of which the database stores the SHA-256 digest.

Bootstrap the first administrator:

```bash
cd backend
python -m app.cli.bootstrap --email you@example.com
# password is prompted, never passed as an argument
```

Then, from a client:

1. `GET /api/v1/auth/csrf` — receive the token and its cookie.
2. `POST /api/v1/auth/login` with that token in `X-CSRF-Token`.
3. `GET /api/v1/auth/me` — identity plus the workspaces you may act in.
4. Send `X-Workspace-Id` on tenant-scoped requests; the server checks it against
   your memberships and binds the tenant for RLS.

```python
from app.api.deps import get_current_tenant, require_approval_authority

@app.get("/proposals")
async def proposals(
    scope: TenantScope = Depends(require_approval_authority),
) -> ...:
    ...
```

`X-Workspace-Id` is a hint, not authority: a workspace you do not belong to is a
403. Capability checks read from one matrix (`RoleCapabilities`), and the
integration role can never approve.

---

## Environment variables

Copy `.env.example` to `.env` and fill in real values. `.env` is git-ignored;
`.env.example` carries no secrets.

Config **fails fast** on placeholder secrets and on plaintext connection
strings. A `redis://` URL is rejected because Upstash requires TLS and the
failure would otherwise appear deep inside a worker rather than at startup.

Two settings from D-017 are worth reading before either flow is exercised:

| Variable | Why it matters |
| --- | --- |
| `MAIL_DELIVERY` | Only `outbox` exists. Messages are written to `email_outbox` with `sent_at` NULL and go nowhere else. |
| `APP_BASE_URL` | The host the emailed link points at. Left unset, a reset sends people to the API's own hostname. |
| `RESET_MAX_REQUESTS`, `RESET_IP_MAX_REQUESTS` | A separate budget from the login counters, so nobody can lock an account out by asking for its password to be reset. |

---

## Security posture

- **Credentials are never committed.** Every secret exposed during setup was in
  plaintext and must be rotated.
- **Edge masking is mandatory (D-010).** Configured key names and secret-shaped
  values are redacted before any log or trace, applied in the structlog
  processor chain so it covers traces, errors, and exception context alike.
- **Telemetry stays in-house.** Self-hosted Langfuse only. LangSmith and
  third-party log aggregators are prohibited by decision, because catalogs
  contain proprietary manufacturer files, wholesale margins, and marketplace
  listings.
- **Marketplace credentials are encrypted at rest** with a per-environment
  Fernet key, never stored as plaintext.
- **Tenant isolation does not depend on any single layer** (D-011). Schema
  constraints, the repository, and row-level security each hold for the cases
  the others miss.
- **Audit logs are immutable in the database** (D-013). A trigger rejects
  `UPDATE` and `DELETE` for every role, so the trail cannot be edited even by
  the application.
- **Sessions are revocable** (D-014). Server-side sessions, not JWTs, so a
  removed membership or a stolen cookie takes effect immediately.
- **Every login failure looks identical** (D-014). Same status, same body, and
  the same hashing cost whether or not the address exists, so responses cannot
  be used to enumerate accounts.
- **Login attempts are throttled twice** (D-014) — per account and per IP —
  because neither alone stops guessing and account spraying.
- **A reset request reveals nothing** (D-017). Same 202, same body, same hashing
  cost whether or not the address exists, and the response says the message was
  queued rather than sent.
- **A reset ends every session** (D-017) and issues none. Revoking only the
  requesting device leaves whatever prompted the reset still signed in.
- **Invitations grant only what the inviter chose** (D-017). The role comes from
  the stored row and acceptance takes no role parameter, and an existing
  account's password is never overwritten by accepting one.

---

## Decisions

Every architectural decision, its alternatives, reasoning, and accepted
trade-offs are recorded in [`DECISIONS.md`](DECISIONS.md).

Current entries: UI/UX direction (D-001), source authority (D-002), personas and
approval authority (D-003), the integration-admin role (D-004), MVP scope (D-005),
vector store (D-006), LLM provider (D-007), job queue (D-008), database hosting
(D-009), observability (D-010), tenant isolation (D-011), soft deletion and
version history (D-012), append-only audit logs (D-013), authentication (D-014),
the catalog domain (D-015), the risk engine (D-016), account recovery and
invitations (D-017), catalog ingestion (D-018), the canonical product model
(D-019), reconciliation (D-020), and the compliance capability gate (D-021).

---

## MVP scope

Section 71 of the master prompt, adopted verbatim. No live marketplace
integrations. The connector abstraction will be built against a mock adapter so
the seam is proven, but real rate limits and partial-failure modes stay
unvalidated until the connector phase.