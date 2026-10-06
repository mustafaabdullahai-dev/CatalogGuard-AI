# Phase 16 — Risk engine

**Status:** Partially built — scoring logic exists and is written by reconciliation; this phase's own work has not happened
**§64 scope:** Risk engine

> Retroactive stub written 2026-10-06. This page states scope, entry conditions
> and exit criteria only. It makes no delivery claims: nothing for this phase
> has been built.

## Governing sections

- §16 Risk engine
- §37 AI confidence

## Entry conditions

- D-016 decided the engine: fixed weights with floors, confidence clamped into (0, 1) and never stored as 0 or 1, `numeric_score` clamped to 0..100.
- Phase 12 already calls `risk.assess` and stores `risk_assessments`.

## Exit criteria

- Weights, floors and bands are confirmed against §16 rather than only against D-016.
- Risk level gates approver count, once Phase 18 defines it.
- Contributing factors remain stored beside the score so the raw total is recoverable.

## Notes

- Implemented early, in `21eee9e`, because Phase 12 needed a score to write.
- `docs/ARCHITECTURE.md` notes the weights are opinionated and do not cross-interact — a deliberate choice so a reviewer can check the arithmetic.
