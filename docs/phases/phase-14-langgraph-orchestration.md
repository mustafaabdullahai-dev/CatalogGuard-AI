# Phase 14 — LangGraph orchestration

**Status:** Not started — this is the next phase
**§64 scope:** LangGraph orchestration

> Retroactive stub written 2026-10-06. This page states scope, entry conditions
> and exit criteria only. It makes no delivery claims: nothing for this phase
> has been built.

## Governing sections

- §6 Agent architecture
- §7 Multi-agent / specialized node design
- §24 LangGraph state
- §35 LLM strategy
- §36 Structured output

## Entry conditions

- Phase 13 complete — the compliance corpus can be retrieved with citations.
- A working LLM provider. D-007 chose Qroq behind an OpenAI-compatible client with a mandatory JSON-schema validation layer and repair-and-retry; that layer has never run against a live endpoint.
- `app/agents/{state,nodes,tools}` exist as empty packages — the intended home for this phase.

## Exit criteria

- A graph whose nodes are the §7 roles, with state typed and serializable (§24).
- Every model call passes the D-007 validation layer; a schema failure is repaired or surfaced, never accepted.
- The graph never writes catalog data (§5): it proposes, and deterministic services decide.
- Streaming/status is observable enough for the frontend's agent activity view (§30).

## Notes

- `langgraph` 1.2.12 and `langchain` 1.4.3 are already installed in `backend/.venv`.
- Nothing in `app/agents/` does anything yet.
