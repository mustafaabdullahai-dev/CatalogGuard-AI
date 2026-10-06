# Phase 19 — Marketplace connectors

**Status:** Deferred by D-005, with the seam still in scope
**§64 scope:** Marketplace connectors

> Retroactive stub written 2026-10-06. This page states scope, entry conditions
> and exit criteria only. It makes no delivery claims: nothing for this phase
> has been built.

## Governing sections

- §22 Marketplace connector architecture
- §23 Tool-call architecture

## Entry conditions

- Phase 20 needs a seam to execute through.
- D-005 defers every live marketplace integration until the core model is stable.

## Exit criteria

- `MarketplaceConnector` abstraction plus one mock adapter, so the seam is proven (D-005's explicit requirement).
- Credentials encrypted at rest with `ENCRYPTION_KEY`, never plaintext (§33).
- No live calls in the MVP: real rate limits and partial-failure modes stay unvalidated and are labelled as such.

## Notes

- `app/connectors/` exists and is empty.
- The demo scenario (§69) and any 'execute' claim must be described as mock-only until this phase runs against a real API.
