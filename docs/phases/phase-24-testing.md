# Phase 24 — Testing

**Status:** In progress — 814 tests (769 unit, 42 agent, 3 opt-in live); integration and e2e directories are empty
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
- Agent tests for the graph — 12 arrived with Phase 14; what remains is coverage of the graph's edges with real tools (Phase 15) and of a run end to end.
- End-to-end tests for the demo scenario (§69).
- API tests against the OpenAPI contract, not only in-process status codes (§42).

## Notes

- `backend/tests/integration` and `backend/tests/e2e` contain only `__init__.py`; `backend/tests/agent` carries the Phase 14 graph and live-provider tests.
- Every existing claim of coverage is unit-level with a mocked database.
