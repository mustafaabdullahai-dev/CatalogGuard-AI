# CatalogGuard AI

**Autonomous e-commerce catalog reconciliation and change-safety platform.**

The LLM proposes. Deterministic code validates. Rules constrain. The sandbox
simulates. Humans approve. Connectors execute. Logs remember.

---

## Current status

**Phases 8–17: authentication, catalog domain, conflicts, risk, ingestion, the
canonical model, reconciliation, the compliance corpus, LangGraph orchestration,
AI tools and sandbox simulation.** Sign-in, tenant isolation, the catalog domain
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
(§18, §19) are complete and tested — **867 tests**, of which three are opt-in
live-provider tests.

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
| Email/password sign-in, Argon2id, server-side sessions (D-014) | Implemented, unit tested |
| Password reset: single-use tokens, session revocation, outbox delivery (D-017) | Implemented, unit tested |
| Workspace invitations: role bound at issue, admin-gated (D-017) | Implemented, unit tested |
| Catalog normalization with an auditable alias table (D-015) | Implemented, unit tested |
| Product identity resolution; only exact identifiers auto-link (D-015) | Implemented, unit tested |
| Parent-child validation: orphans, cycles, variant axes, bundles (D-015) | Implemented, unit tested |
| Source authority with per-field overrides; ties escalate (D-015) | Implemented, unit tested |
| Conflict detection with available values, evidence and impact (§11) | Implemented, unit tested |
| Deterministic risk scoring: 22 weights, four bands, five floors, §16 conformance audited (D-016) | Implemented, unit tested |
| Catalog ingestion: validation, CSV/JSON/Excel parsing, schema detection, raw storage (D-018) | Implemented, 44 tests |
| Ingestion sync/async seam (Dramatiq worker) | Implemented; **unverified** — no live Redis |
| Canonical product model: raw records → products, identifiers, v1 versions (D-019) | Implemented, 12 tests |
| Catalog anchor (auto Default catalog per workspace) | Implemented, unit tested |
| `source_records.product_id` provenance column (migration regenerated) | Implemented, parity tested |
| Reconciliation engine: source authority → conflicts → risk-scored proposals (D-020) | Implemented, 12 tests |
| Change proposals, risk assessments, runs | Implemented, unit tested |
| Re-run supersede (no duplicate approval queue) | Implemented, unit tested |
| Compliance corpus: section-aware chunking, content-hash dedup (D-021) | Implemented, 52 tests |
| Hybrid retrieval: GIN keyword `ts_rank` + pgvector cosine, deterministic rerank | Implemented, unit tested |
| Compliance API: ingest / list / search with per-hit citations and a signal note | Implemented, 12 tests |
| Embeddings: provider port with a null adapter | Implemented; **no embedding provider works** — `/embeddings` answered `model_not_found` for every candidate id (`504009f`), so retrieval runs keyword-only |
| RBAC guards and tenant binding wired to the session | Implemented, unit tested |
| CSRF (signed double-submit) and security headers | Implemented, in-process tested |
| Tenant isolation: composite FKs, tenant-scoped repository, RLS policies (D-011) | Implemented, unit tested |
| Append-only audit logs enforced by trigger (D-013) | Implemented, unit tested |
| Initial migration, generated from ORM metadata | Offline SQL verified |
| Postgres (Neon) | TLS verified; **not yet applied**, awaiting rotation |
| Redis (Upstash) | **Verified live** — Redis 8.4.0, TLS required |
| LLM (Qroq) | **Wired and live-verified** — fast (`openai/gpt-oss-20b`) and reasoning (`openai/gpt-oss-120b`) confirmed against the endpoint; key still needs rotation |
| Agent graph (§7 roles, §24 state, D-007 validation layer) | Implemented, 12 unit tests + 2 opt-in live tests |
| Agent tools: normalize_field, resolve_source_authority, resolve_identity, assess_change_risk (§23) | Implemented, 30 tests + 1 opt-in live tool-call test; 4 of §23's 18 tools |
| Sandbox simulation: §18 chain on a copy, §19 diff, `POST /change-proposals/{id}/simulate` | Implemented, 31 tests; compliance stage skipped (no corpus), route untested against a database |
| Frontend: dashboard, catalog explorer, approval center | Implemented, unit tested |
| Outbound mail transport | Not configured; messages queue to `email_outbox` (D-017) |

Users cannot self-register: the first administrator is created by a CLI, and
every subsequent user arrives through an invitation an administrator issues.
A lost password is resolved with `POST /api/v1/auth/password-reset`. Both were
gaps in D-014 and are closed in D-017.

Neither flow sends mail yet. Both queue a message to `email_outbox` and stop,
because no mail provider is configured. `python -m app.cli.outbox` prints the
queued messages, which is how the flows are exercised end to end offline.

The migration has never been applied to a live database. It has been rendered
to SQL in both directions and asserted against the ORM, which catches drift and
malformed DDL but cannot prove that Neon accepts it. Treat every capability
above as *implemented but not verified* until the corresponding phase runs.

---

## Blockers

Three items gate the next phase, all requiring action outside the codebase:

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
python -m pytest -q                   # 864 passed, 3 skipped, no database required
CATALOGGUARD_LIVE_LLM=1 python -m pytest tests/agent -q   # opt-in: 3 live LLM calls (2 schemas + 1 tool call)
python -m ruff check app tests alembic scripts
python -m mypy app
```

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