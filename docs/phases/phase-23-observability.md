# Phase 23 — Observability

**Status:** Not started (D-010 decided the approach; nothing has been exported)
**§64 scope:** Observability

> Retroactive stub written 2026-10-06. This page states scope, entry conditions
> and exit criteria only. It makes no delivery claims: nothing for this phase
> has been built.

## Governing sections

- §32 Observability
- §55 Logging
- §56 Cost control
- §39 Auditability

## Entry conditions

- D-010: self-hosted Langfuse only. No LangSmith, no third-party log aggregator — catalogs contain proprietary data.
- Edge masking (D-010) and `llm_usage` tables already exist.

## Exit criteria

- Traces exported to Langfuse and readable per run.
- Cost control per §56: budget alerts, per-model usage, `llm_usage` written by the caller that knows which run asked for the tokens.
- Audit trail queryable end to end (§39).

## Notes

- `LANGFUSE_ENABLED=false` in `.env`; no trace has ever been exported.
