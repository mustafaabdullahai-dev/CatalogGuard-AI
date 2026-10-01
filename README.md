# CatalogGuard AI

**Autonomous e-commerce catalog reconciliation and change-safety platform.**

The LLM proposes. Deterministic code validates. Rules constrain. The sandbox
simulates. Humans approve. Connectors execute. Logs remember.

---

## Current status

**Phase 5–6: repository scaffold and development environment.** Backend
foundation is in place. Catalog domain logic has not been written yet.

| Component | State |
|---|---|
| Repository structure | Scaffolded |
| Python 3.13 venv + pinned dependencies | Installed |
| Settings, logging, edge masking | Implemented, unit tested |
| FastAPI app shell, `/health`, `/ready`, OpenAPI | Running and verified |
| Postgres (Neon) | TLS verified; authenticated connection pending |
| Redis (Upstash) | **Verified live** — Redis 8.4.0, TLS required |
| LLM (Qroq) | Not wired; needs a rotated API key |
| Catalog domain, agents, frontend | Not started |

Nothing here has been tested against a live database. Treat every capability
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
python -m pytest -q      # unit suite
python -m ruff check app tests
```

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

---

## Decisions

Every architectural decision, its alternatives, reasoning, and accepted
trade-offs are recorded in [`DECISIONS.md`](DECISIONS.md).

Current entries: UI/UX direction (D-001), source authority (D-002), personas and
approval authority (D-003), the integration-admin role (D-004), MVP scope (D-005),
vector store (D-006), LLM provider (D-007), job queue (D-008), database hosting
(D-009), and observability (D-010).

---

## MVP scope

Section 71 of the master prompt, adopted verbatim. No live marketplace
integrations. The connector abstraction will be built against a mock adapter so
the seam is proven, but real rate limits and partial-failure modes stay
unvalidated until the connector phase.