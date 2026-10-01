# CatalogGuard AI

**Autonomous e-commerce catalog reconciliation and change-safety platform.**

The LLM proposes. Deterministic code validates. Rules constrain. The sandbox
simulates. Humans approve. Connectors execute. Logs remember.

---

## Current status

**Phase 8: authentication and authorization.** Schema, tenant isolation, and
sign-in are complete. Catalog domain logic has not been written yet.

| Component | State |
|---|---|
| Repository structure | Scaffolded |
| Python 3.13 venv + pinned dependencies | Installed |
| Settings, logging, edge masking | Implemented, unit tested |
| FastAPI app shell, `/health`, `/ready`, OpenAPI | Unit tested in-process |
| ORM: 30 tables across tenancy, catalog, reconciliation, platform, compliance | Implemented, 325 tests |
| Email/password sign-in, Argon2id, server-side sessions (D-014) | Implemented, unit tested |
| RBAC guards and tenant binding wired to the session | Implemented, unit tested |
| CSRF (signed double-submit) and security headers | Implemented, in-process tested |
| Tenant isolation: composite FKs, tenant-scoped repository, RLS policies (D-011) | Implemented, unit tested |
| Append-only audit logs enforced by trigger (D-013) | Implemented, unit tested |
| Initial migration, generated from ORM metadata | Offline SQL verified |
| Postgres (Neon) | TLS verified; **not yet applied**, awaiting rotation |
| Redis (Upstash) | **Verified live** — Redis 8.4.0, TLS required |
| LLM (Qroq) | Not wired; needs a rotated API key |
| Catalog domain, agents, frontend | Not started |

Users cannot self-register. The first administrator is created by a CLI, and
there is no password reset yet — both are deliberate gaps, recorded in D-014.

The migration has never been applied to a live database. It has been rendered
to SQL in both directions and asserted against the ORM, which catches drift and
malformed DDL but cannot prove that Neon accepts it. Treat every capability
above as *implemented but not verified* until the corresponding phase runs.

---

## Blockers

Three items gate the next phase, all requiring action outside the codebase:

1. **Rotate the Qroq API key.** It was shared in plaintext. Put the new value in
   `.env` as `QROQ_API_KEY`. Config refuses to start while it is a placeholder.
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
│   │   ├── agents/       LangGraph orchestration (nodes, tools, state)
│   │   ├── api/v1/       FastAPI routers
│   │   ├── core/         config, logging, masking
│   │   ├── db/           engine, session, models
│   │   ├── rag/          compliance retrieval
│   │   ├── services/     deterministic domain logic
│   │   ├── workers/      Dramatiq actors
│   │   └── main.py       ASGI factory
│   ├── alembic/          migrations
│   ├── tests/            unit, integration, agent, e2e
│   └── pyproject.toml
├── frontend/             Next.js (not yet scaffolded)
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
python -m pytest -q                   # unit suite, no database required
python -m ruff check app tests alembic scripts
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
rather than everything. Until authentication lands in Phase 8, nothing binds a
tenant in production, which means tenant queries fail closed until then.

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

---

## Decisions

Every architectural decision, its alternatives, reasoning, and accepted
trade-offs are recorded in [`DECISIONS.md`](DECISIONS.md).

Current entries: UI/UX direction (D-001), source authority (D-002), personas and
approval authority (D-003), the integration-admin role (D-004), MVP scope (D-005),
vector store (D-006), LLM provider (D-007), job queue (D-008), database hosting
(D-009), observability (D-010), tenant isolation (D-011), soft deletion and
version history (D-012), append-only audit logs (D-013), and authentication
(D-014).

---

## MVP scope

Section 71 of the master prompt, adopted verbatim. No live marketplace
integrations. The connector abstraction will be built against a mock adapter so
the seam is proven, but real rate limits and partial-failure modes stay
unvalidated until the connector phase.