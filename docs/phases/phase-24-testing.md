# Phase 24 — Testing

**Status:** In progress — 764 unit tests; integration, agent and e2e directories are empty
**§64 scope:** Testing

> Retroactive stub written 2026-10-06. This page states scope, entry conditions
> and exit criteria only. It makes no delivery claims: nothing for this phase
> has been built.

## Governing sections

- §40 Testing strategy
- §42 API testing

## Entry conditions

- Any phase can be tested; the gap is *which kinds* of test exist.

## Exit criteria

- Integration tests against a real database for the paths currently mocked: auth, ingestion, canonicalization, reconciliation, retrieval.
- Agent tests for the graph once Phase 14 lands.
- End-to-end tests for the demo scenario (§69).
- API tests against the OpenAPI contract, not only in-process status codes (§42).

## Notes

- `backend/tests/integration`, `backend/tests/agent` and `backend/tests/e2e` contain only `__init__.py`.
- Every existing claim of coverage is unit-level with a mocked database.
