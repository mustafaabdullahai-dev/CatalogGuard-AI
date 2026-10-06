# Phase 14 — LangGraph orchestration

**Status:** Complete — verified offline and against the live provider
**§64 scope:** LangGraph orchestration
**Decisions:** [D-007](../../DECISIONS.md) LLM provider and mandatory validation layer,
[D-004](../../DECISIONS.md) free-tier throttling, [D-005](../../DECISIONS.md) where AI is allowed to act
**Commit:** `6268cc0` (2026-10-06)

## What this phase delivered

One run of catalog analysis, from a payload to validated proposals:

    START → intake → normalize → analyze → finalize → END

- **No fall-through is topology, not a convention.** Each model node has a
  conditional edge: the moment a node records an error the run goes straight to
  `finalize`, so the next node has no incoming edge from that path. D-007 #3 is
  then something the compiler enforces rather than something a reader has to
  notice.
- **State is §24's shape, bounded to this phase.** `CatalogState` carries only
  what these nodes read or write. The keys for compliance, risk, simulation,
  approval and execution are absent until the phases that produce them arrive —
  carrying them now would mean every node inventing a placeholder for work that
  never happened, and a placeholder is indistinguishable from a result later.
- **Node outputs are Pydantic models (§36).** A value that fails validation
  never reaches the next node; free-form dicts do not travel between nodes.
- **D-007's validation layer actually runs.** The JSON Schema is stated in the
  prompt (the provider is OpenAI-*compatible*, and D-007 records that its
  provider-side enforcement is materially weaker), a failure is re-asked with
  the validation error attached, and after the bounded repairs a
  `StructuredOutputError` carries attempts and tokens. The graph stops rather
  than reasoning over a value nobody validated. Repairs are counted in usage,
  because a repair re-spends the same input and cost control that ignores it
  under-reports the burn.
- **Routing follows §35.** Intake and normalization take the fast tier;
  reconciliation takes the reasoning tier. Paying the reasoning model to
  classify a payload is the failure §35 exists to prevent.
- **`finalize` is plain Python.** Deciding whether a run succeeded is where
  §6's line between AI reasoning and deterministic work is drawn most clearly,
  and nothing in `app/agents/` touches the database, the queue or the
  filesystem.

## Where it lives

- `app/agents/state/models.py` — `CatalogState`, `IntakeReport`,
  `NormalizationPlan`, `ReconciliationOutput`, `AgentError`, `Usage`
- `app/agents/client.py` — `ChatCompletionClient` (schema-first prompt, bounded
  repair, tier routing), `ChatModelCompleter` (LangChain transport)
- `app/agents/nodes/core.py` — the §7 roles plus `finalize` and `route`
- `app/agents/graph.py` — `build_graph()`, `run_graph()`
- `tests/agent/test_graph.py` — 12 tests, no network
- `tests/agent/test_llm_live.py` — 2 tests, opt-in (`live_llm` marker)

## Verification

| Check | Result |
|---|---|
| `pytest` | **781 passed, 2 skipped** (12 of them in `tests/agent`) |
| `CATALOGGUARD_LIVE_LLM=1 pytest tests/agent/test_llm_live.py` | **2 passed** |
| `ruff check app tests alembic scripts` | clean |
| `mypy app` | clean, 89 files |

The default suite spends nothing: the live tests are behind an explicit opt-in
and skip without it, so a scheduled run can never call a third party on a
decision nobody made.

**The live run.** Against the real endpoint, the fast tier returned an
`IntakeReport` that validated on the first attempt, and the reasoning tier
returned a `ReconciliationOutput` that validated too — with real token counts
back from the provider, not zeros. This is the evidence D-007 had been missing:
the schema-first prompt, the parse and the validation all hold against answers
nobody scripted.

## A bug the live run found

The second live test failed with `Event loop is closed` on a request that never
left the machine. LangChain's `ChatOpenAI` falls back to an `@lru_cache`d
process-wide `httpx.AsyncClient` when none is supplied, so the first event loop
to use it becomes the only loop that may — a second run drives a transport bound
to a dead one. The transport is now owned by `ChatModelCompleter`, which
`ChatCompletionClient.aclose()` releases, and a client the graph creates for a
single run is closed before that run returns. Scripted tests could never have
found this: nothing about it is visible without a second real call.

## Exit criteria

| Criterion | Status |
|---|---|
| Nodes are the §7 roles, state typed and serializable (§24) | Met |
| Every model call passes D-007's layer; failure repaired or surfaced, never accepted | Met |
| The graph never writes catalog data (§5) — it proposes, deterministic services decide | Met |
| Streaming/status observable enough for the agent activity view (§30) | **Partially met** — see below |

On the last point: a run returns its final state and logs one structured
summary (`catalog_agent_run_finished` with status, error count and usage), and
`build_graph()` hands back LangGraph's compiled graph, so a later API can
consume `astream(stream_mode="updates")` for per-node progress. What does not
exist yet is any HTTP surface for it — there is no agent run endpoint in
`app/api/v1/`, and the agent activity view of §30 belongs to Phase 21. The
streaming seam exists; nothing is streaming through it.

## Gaps and honest limits

- **No tool nodes.** `app/agents/tools/` is still empty: the graph reads its
  state and proposes, and cannot call anything. Phase 15 gives it tools.
- **Nothing persists.** No run, proposal or usage record is written to a
  database. There is no run history to query and no id to query it by.
- **The repair loop has never been triggered by a real model.** Its failure
  path, its prompt and its abort are proven with scripted replies only; the
  live tests exercise the success path, because a provider that answers
  correctly on demand cannot be relied on to answer wrongly on demand.
- **Two model ids, both confirmed.** `openai/gpt-oss-20b` (fast) and
  `openai/gpt-oss-120b` (reasoning) are the only completion ids this provider
  served at the time of the run; the previously configured
  `llama-3.3-70b-versatile` does not exist there (see `504009f`).
- **`run_graph` has no caller yet.** It is exercised by tests and awaits the
  API layer that will own a run's lifecycle, including the client's.
