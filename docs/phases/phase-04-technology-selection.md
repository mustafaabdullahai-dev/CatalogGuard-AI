# Phase 04 — Technology selection

**Status:** Complete
**§64 scope:** Technology selection
**Decisions:** [D-006](../../DECISIONS.md) vector store · [D-007](../../DECISIONS.md)
LLM provider · [D-008](../../DECISIONS.md) job queue · [D-009](../../DECISIONS.md)
database hosting · [D-010](../../DECISIONS.md) observability and data residency

## What this phase delivered

| Area | Choice | The line that matters |
|---|---|---|
| Vector store | PostgreSQL + pgvector (D-006) | No second service, no second source of truth — compliance retrieval filters by marketplace, category, version and date, and those are relational. |
| LLM provider | Qroq, OpenAI-compatible, with a mandatory validation layer (D-007) | Every call is wrapped in strict JSON-schema validation with repair-and-retry. The provider is behind a port. |
| Job queue | Dramatiq + Redis (D-008) | Long work leaves the request; short work stays inline. |
| Hosting | Neon Postgres + Upstash Redis (D-009) | TLS required on both; `redis://` is rejected at config load because the failure would otherwise surface inside a worker. |
| Observability | Self-hosted Langfuse only (D-010) | No LangSmith, no third-party log aggregator. Catalogs contain proprietary manufacturer files, wholesale margins and marketplace listings. |

## Where it lives

- `app/core/config.py` — settings with fail-fast validators: placeholder
  secrets rejected, plaintext connection strings rejected, `redis://` rejected.
- `.env.example` — the template; `.env` is git-ignored.

## Verification

- `tests/unit/test_config.py` — 18 tests over the fail-fast validators.
- `tests/unit/test_masking.py` — 15 tests over edge masking (D-010): configured
  key names and secret-shaped values redacted before anything is logged.
- **Redis: verified live** — Redis 8.4.0, TLS.
- **Neon: TLS verified, database never used.** The migration has not been
  applied.
- **Qroq: never called.** D-007's validation layer has no live run behind it.

## Gaps and honest limits

- The `qroq` embedding path and every model call are unproven until a key
  works against the endpoint.
- Langfuse is specified and disabled (`LANGFUSE_ENABLED=false`); no trace has
  ever been exported.
- Embedding dimension is fixed at 1536 in the schema, so choosing a different
  embedding model is a migration, not a setting.
