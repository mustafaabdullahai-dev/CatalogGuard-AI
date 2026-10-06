# Phase 17 — Sandbox simulation

**Status:** Not started
**§64 scope:** Sandbox simulation

> Retroactive stub written 2026-10-06. This page states scope, entry conditions
> and exit criteria only. It makes no delivery claims: nothing for this phase
> has been built.

## Governing sections

- §18 Sandbox simulation engine
- §19 Diff view
- §51 Rollback

## Entry conditions

- Phase 12 writes proposals with `validation_status='pending'` and no sandbox has run.
- `simulation_runs` exists in the schema and is never written.

## Exit criteria

- A proposal's predicted downstream impact is computed deterministically and stored.
- `validation_status` moves off `pending` as a result of a run, not by default.
- The diff view (§19) has data to render: before / proposed / impact.

## Notes

- Until this phase exists, every proposal sits at `pending` and the approval UI can only show intent.
