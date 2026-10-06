# Phase 27 — Performance testing

**Status:** Not started
**§64 scope:** Performance testing

> Retroactive stub written 2026-10-06. This page states scope, entry conditions
> and exit criteria only. It makes no delivery claims: nothing for this phase
> has been built.

## Governing sections

- §57 Performance
- §58 Frontend performance
- §53 Catalog health

## Entry conditions

- A live database to measure against.

## Exit criteria

- The §57 rule about N round trips enforced by measurement, not only by review — the list path currently does one grouped count for chunk totals.
- Retrieval candidate generation profiled at corpus sizes beyond the per-workspace policy set.
- Frontend metrics per §58.

## Notes

- Profiling of `build_observations` re-normalizing per run was explicitly deferred to this phase in D-019/D-020.
