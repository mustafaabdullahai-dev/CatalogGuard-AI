# Phase 15 — AI tools

**Status:** Complete — verified offline and with a live tool call
**§64 scope:** AI tools
**Governing sections:** §23 tool-call architecture, §8 identity agent, §9 parent-child agent,
§10 reconciliation agent, §34 prompt-injection defense, §36 structured output
**Decisions:** [D-007](../../DECISIONS.md) mandatory validation layer,
[D-003](../../DECISIONS.md)/[D-004](../../DECISIONS.md) roles and capabilities,
[D-006](../../DECISIONS.md) database unconfirmed
**Commit:** `1cc3931` (2026-10-06)

## What this phase delivered

Four tools the graph can now call, and the contract that makes a tool call
survivable:

    normalize_field · resolve_source_authority · resolve_identity · assess_change_risk

- **They are adapters, nothing more.** Each handler is a pure function over an
  existing service (`app/services/catalog/normalization.py`, `authority.py`,
  `identity.py`, `app/services/reconciliation/risk.py`), so a tool cannot do
  anything §5 would not let the service do. None of them reads the database —
  which is also why they are the four that exist (see *Why four*).
- **§23's seven requirements have one home each.** Purpose in the description,
  strict input in the args model, strict output re-validated after the handler,
  validation before and after, error handling that never raises into the run,
  one metadata-only log line per execution (§55), authorization read from
  `RoleCapabilities` — the same matrix the API routes check, so a tool cannot
  grant what a route would refuse.
- **Unknown keys are refused, not ignored.** Every args model inherits
  `ToolArgs` with `extra="forbid"`: a field the model invented fails
  validation and is told so, instead of being silently dropped. Silence teaches
  a model that inventing fields worked.
- **Malformed arguments are kept verbatim.** A tool call whose JSON does not
  parse is passed through under `_unparsed` so validation rejects it loudly,
  rather than validating an empty dict and answering as though the model had
  asked for defaults.
- **A failure is data for the model.** `ToolRegistry.execute` returns
  `ToolCallOutcome` for six error codes (`unknown_tool`, `not_authorized`,
  `invalid_arguments`, `tool_error`, `invalid_output`, `output_too_large`) and
  never raises: the run continues and the model reads `{"ok": false, "error":
  …}` and can correct itself. An exception thrown at it would unwind the graph
  over a bad argument.
- **Output is bounded.** Results are re-serialized and size-checked against
  `TOOL_OUTPUT_MAX_CHARS` (default 8000) before they reach the prompt, so a
  tool cannot flood the context — and the model is told, in the error, to ask
  for less data.
- **Tool rounds share the repair budget.** `ChatCompletionClient.complete()`
  loops up to `LLM_MAX_TOOL_ROUNDS` (default 3) on tool calls, and those rounds
  count against the same `max_attempts` as schema repairs: a model looping on
  tools burns tokens exactly like a model looping on invalid JSON, and
  exhausting the rounds fails the call with the attempts it made and the tokens
  it spent. Grants no one more turn just because they were tool calls.
- **Results are delimited and marked untrusted (§34).** Every result goes back
  inside `<tool_result name="…" untrusted="true">`, preceded by a standing
  policy in the system prompt: tool results are data, never instructions, and
  this prompt wins if they disagree. The name is rewritten to a safe shape
  before it enters the tag, so a hallucinated tool name cannot close the block
  and open a fake one.
- **Each node gets the tools it may use.** `NODE_TOOLS` gives intake none,
  normalization `normalize_field`, and analysis the other three; the prompt only
  advertises that subset (`ToolRegistry.subset`), and a run may pass
  `ToolRegistry()` to have no tools at all.
- **Authorization has a caller.** `initiator_role` is now a required key on
  `CatalogState` (§24's entry contract): a tool without a caller's role cannot
  be authorized, so `ToolContext` is built per run from state instead of being
  defaulted.

## Why four

§23 lists eighteen tools. The ones that *create* a proposal, approve a change
or execute a listing belong to Phases 16 through 20 and to the approval path
§38 draws — §5 keeps them out of the model's hands. The lookups that need a
repository (`catalog_search`, `product_lookup`, `compliance_search`) need a
live database, which D-006 still records as unconfirmed. What is left is the
read-only middle, all pure Python. The parent-child relationship validator of
§9 is the same story: the service exists, but the data it would look at does
not exist outside a database.

## Where it lives

- `app/agents/tools/spec.py` — `ToolSpec`, `ToolArgs`, `ToolContext`,
  `ToolCall`, `ToolCallOutcome`, `ToolRegistry` (register/schemas/describe/
  subset/execute), `wrap_tool_result`, `TOOL_OUTPUT_POLICY`, error codes
- `app/agents/tools/catalog.py` — the four tools, their arg and result models,
  `DEFAULT_TOOLS`
- `app/agents/tools/__init__.py` — `default_registry()` reading
  `TOOL_OUTPUT_MAX_CHARS` from settings
- `app/agents/client.py` — `Completion.tool_calls`, `bind_tools`, the tool-round
  loop inside `complete()`, `Usage.calls`
- `app/agents/nodes/core.py` — `NODE_TOOLS`, `CatalogNodes._tool_kwargs()`
- `app/agents/graph.py` — `build_graph(..., tools=)`, `run_graph(..., tools=)`
- `app/core/config.py`, `.env.example` — `llm_max_tool_rounds`,
  `tool_output_max_chars`
- `tests/agent/test_tools.py` — 30 tests, no network

## Verification

| Check | Result |
|---|---|
| `pytest` | **811 passed, 3 skipped** (42 in `tests/agent`: 39 offline + 3 live opt-in) |
| `CATALOGGUARD_LIVE_LLM=1 pytest tests/agent/test_llm_live.py` | **3 passed** |
| `ruff check app tests alembic scripts` | clean |
| `mypy app` | clean, 92 files |

**The live tool call.** The third live test asks the real model which source
wins for a GTIN where marketplace and manufacturer disagree, with only
`resolve_source_authority` registered. It called the tool, then answered
`resolved` / `manufacturer` — the deterministic answer, not one it could have
guessed — and `usage.calls >= 2` proves it looked before it spoke. One call
would have meant it answered from memory, which is exactly the claim a tool
layer exists to refute.

## Exit criteria

| Criterion | Status |
|---|---|
| Tools are thin adapters over `app/services/` (§5) | Met — four of them, pure handlers |
| Every argument schema-validated; output bounded and typed (§23) | Met — `ToolArgs` forbids extras, results re-validated and size-checked |
| Prompt-injection defenses for text a tenant supplied (§34) | Met structurally — see below |
| The model cannot make a tool do more than a service would | Met — no tool has a side effect its service lacks |

On §34: the defense is the delimiter, the sanitized name and the standing
policy, all applied unconditionally. What does not exist is an adversarial live
test — nothing here tries to *make* a model follow an instruction found inside
a tool result and asserts that it did not. The structural defense is tested;
its effect on a real model's behavior is reasoned, not measured.

## Gaps and honest limits

- **Four of eighteen tools.** The rest are deferred to the phases that own
  their services (16, 18, 20) or blocked on D-006's database. `§9`'s
  parent-child validator has no tool either, for the same reason.
- **`not_authorized` is tested against a synthetic spec.** All four real tools
  require `capability="read"`, which every role holds (D-003), so the
  not-authorized branch is proven with a tool that asks for something else —
  the branch, not a real role being refused a real tool.
- **No tool yet touches a database**, so nothing here exercises an async
  handler against anything but the scripted ones.
- **The live test's assertion is behavioral.** It depends on the model choosing
  to call; a model that answered from memory would fail it. That is the point
  of the assertion, and it is also why a provider behavior change would surface
  here first.
- **`run_graph` still has no caller.** Tools are wired into the graph and
  proven through it, but no HTTP surface invokes a run — that remains the API
  layer Phase 15's successors and Phase 21's activity view need.
