# Phase 20 — Execution engine

**Status:** Not started
**§64 scope:** Execution engine

> Retroactive stub written 2026-10-06. This page states scope, entry conditions
> and exit criteria only. It makes no delivery claims: nothing for this phase
> has been built.

## Governing sections

- §21 Execution engine
- §51 Rollback

## Entry conditions

- Phase 18 approvals, and the Phase 19 seam to execute through.

## Exit criteria

- Only approved operations execute, each with post-execution validation (§21).
- Rollback is designed before the first live write (§51).
- `execution_jobs` and `tool_calls` move from schema to record.

## Notes

- Nothing in the codebase performs a marketplace write today.
