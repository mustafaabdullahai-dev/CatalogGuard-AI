# Phase 15 — AI tools

**Status:** Not started
**§64 scope:** AI tools

> Retroactive stub written 2026-10-06. This page states scope, entry conditions
> and exit criteria only. It makes no delivery claims: nothing for this phase
> has been built.

## Governing sections

- §23 Tool-call architecture
- §8 Product identity resolution agent
- §9 Parent-child relationship agent
- §10 Catalog reconciliation agent
- §36 Structured output
- §34 Prompt-injection defense

## Entry conditions

- Phase 14 gives the graph nodes something to call.
- Deterministic services already exist to wrap: `resolve_identity`, relationship validation, conflict detection, source authority, risk scoring, compliance retrieval.

## Exit criteria

- Tools are thin adapters over `app/services/`, so a tool cannot do anything a service would not.
- Every tool argument is schema-validated, and tool output is bounded and typed (§23).
- Prompt-injection defenses are in place for any tool that carries text the tenant supplied (§34).

## Notes

- No tool is registered anywhere yet.
