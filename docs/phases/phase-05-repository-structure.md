# Phase 05 — Repository structure

**Status:** Complete
**§64 scope:** Repository structure
**Decisions:** — (a consequence of D-005's scope and D-008's queue)

## What this phase delivered

The monorepo layout the whole build sits in, established in `0a4d3e7`:

```
CatalogGuard AI/
├── backend/
│   ├── app/
│   │   ├── agents/        LangGraph orchestration (nodes, tools, state)   ← Phase 14
│   │   ├── api/v1/        FastAPI routers
│   │   ├── cli/           bootstrap, outbox
│   │   ├── connectors/    marketplace connector seam                       ← Phase 19
│   │   ├── core/          config, logging, masking, middleware
│   │   ├── db/            engine, session, repository, RLS, base
│   │   ├── models/        tenancy, catalog, reconciliation, platform
│   │   ├── rag/           compliance retrieval
│   │   ├── schemas/       request/response models
│   │   ├── services/      deterministic domain logic
│   │   ├── static/        API-served brand assets
│   │   └── workers/       Dramatiq actors
│   ├── alembic/           migrations (generated)
│   ├── scripts/           generate_initial_migration
│   ├── tests/             unit, integration, agent, e2e
│   └── pyproject.toml
├── frontend/              Next.js
├── docs/                  ARCHITECTURE.md, phases/
├── docker/                ← empty; Phase 28
├── DECISIONS.md
└── MASTER_PROMPT.md
```

Routers today: `auth`, `brand`, `catalog`, `compliance`, `ingestion`,
`invitations`, `reconciliation`. Services: `auth`, `catalog`, `ingestion`,
`notify`, `reconciliation`.

## Where it lives

The tree itself. `README.md` documents the same layout with running
instructions.

## Verification

Structure claims are checked by the test suite importing across it (781 tests)
and by `tests/unit/test_migration_parity.py`, which fails if the generated
migration and `app/models` disagree.

## Gaps and honest limits

- `backend/tests/integration` and `backend/tests/e2e` are **empty
  directories** with `__init__.py` only; `backend/tests/agent` gained 12
  offline tests and 2 opt-in live ones when Phase 14 landed. 769 of the 781
  tests are unit tests, and no test needs a database or a queue.
- `docker/` is empty — Phase 28.
- `app/agents/` began as a placeholder for Phase 14 and now holds the
  orchestration graph.
