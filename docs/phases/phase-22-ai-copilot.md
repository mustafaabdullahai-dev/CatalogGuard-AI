# Phase 22 — AI Copilot

**Status:** Complete as an API — read-only, tool-backed, cited answers behind one capability; no console surface
**§64 scope:** AI Copilot
**Governing sections:** §31 AI Copilot, §34 prompt-injection defense, §23 tool-call architecture, §33 security, §35 LLM strategy, §37 evidence and confidence
**Decisions:** [D-024](../../DECISIONS.md) (read-only, cited answers, one capability); [D-005](../../DECISIONS.md) amended
**Commit:** `fcebf90`

> This page replaces the 2026-10-06 stub, which said "not started (deferred by
> D-005)". D-005's deferral is lifted here with an explicit boundary rather than
> quietly ignored: what the copilot can do, what it cannot, and where a person
> still stands between a question and a change.

## What this phase delivered

§31 asks for an optional copilot that "must use tools rather than hallucinating
database information". Until now nothing could: four pure tools existed, the
graph was called only from tests, and no tool read a database. This phase makes
the ask possible and makes it checkable.

- **Five tools that read the caller's workspace.** `product_lookup` returns a
  product with the disagreements and proposals behind it (the answer to "why is
  SKU X unsafe to modify"); `conflict_search` finds recorded disagreements with
  their competing values and ranks ("find products with conflicting colours");
  `change_search` filters proposals by risk band, affected channel, field or
  status and orders by risk ("show high-risk changes", "where do Amazon and
  Shopify disagree"); `compliance_search` returns cited chunks with the signals
  that ranked them; `sandbox_simulator` runs §18's chain on a copy. Phase 15's
  four pure tools join them, so "why did the authority rule pick this" is
  answerable too.
- **A bounded turn, reusing the Phase 14 loop.** `ChatCompletionClient.complete`
  already let a model ask for tools before answering, bounded by
  `llm_max_tool_rounds`, wrapped in D-007's schema-first validation with bounded
  repair. The copilot supplies the tools, the schema and the system role; it does
  not invent a second agent loop.
- **Citations are enforced, not requested.** Every citation naming a tool that
  never ran is dropped. An answer left making claims without a usable citation is
  sent back once with the complaint spelled out; a second failure answers 502
  rather than producing a paragraph. `tools_used` in the response is what the
  registry actually executed — the model's own account of its tool use is
  discarded.
- **`insufficient_data` is a first-class answer.** "The tools cannot cover this,
  and here is what is missing" is a successful 200. So is "no products matched"
  from `product_lookup`, which is the point: the alternative is a copilot that
  invents a plausible product when a lookup finds nothing.
- **One capability, one route.** `use_copilot` goes to the ops manager and the
  data steward — the roles that review changes. The merchandiser and SEO
  specialist are refused (their write access is attribute-scoped; the copilot's
  questions are not), and the data-integration admin is refused because holding
  credential custody and asking catalog questions in one account is the
  combination D-003 separated. The router exposes exactly one POST and it cannot
  change anything.
- **Records per turn, no privileges.** One `agent_runs` row, one `tool_calls`
  row per call (Phase 20's recorder, arguments masked), and one immutable
  `copilot.query` audit row carrying the question and the tools that answered it.
- **Untrusted content stays data.** Tool results reach the model inside the
  existing `TOOL_OUTPUT_POLICY`, prior turns are labelled as context rather than
  instructions, and the system role tells the model to report hostile catalog
  content as suspicious rather than act on it (§34).

## Where it lives

- `app/services/copilot/tools.py` — the five catalog tools and the registry builder
- `app/services/copilot/schema.py` — `CopilotAnswer`, `COPILOT_ROLE`
- `app/services/copilot/agent.py` — the turn: bounds, citation enforcement, records, audit
- `app/services/copilot/rate_limit.py` — the per-process budget, and what it is not
- `app/services/simulation/service.py` — §18's chain, extracted so the route and the
  copilot tool run the same simulation instead of two copies
- `app/api/v1/copilot.py` — `POST /api/v1/copilot/query`
- `app/schemas/copilot.py` — request/response models
- `tests/unit/test_copilot_tools.py` (19), `test_copilot_service.py` (20), `test_copilot_api.py` (14)

## Verification

| Check | Result |
|---|---|
| `pytest` | **1006 passed, 3 skipped** (963 unit, 43 agent offline, 3 live opt-in) |
| `ruff check app tests alembic scripts` | clean |
| `mypy app` | clean, 116 files |

## Exit criteria

| Criterion | Status |
|---|---|
| Embedded intelligence with evidence, not a chat widget — D-001's constraint | **Met for the API.** Every answer carries citations to rows or sections, the response repeats what the copilot cannot do, and there is no UI to dress it up as a chat. "Embedded" in the sense D-001 means — inside the evidence flow — is not met: nothing embeds it yet. |
| Copilot available but subordinate to the workflow | **Met.** No tool creates, approves or executes anything; the only write is a simulation run; every answer points at the surfaces a person decides on. |
| §31's example questions answered by tools | **Six of seven have a dedicated tool path** (`product_lookup`, `conflict_search`, `change_search`, `compliance_search`, `sandbox_simulator`). "Simulate changing the *parent* of product Y" is not answered as written: a proposal models one field change, so the sandbox simulates a field, not a re-parenting. That gap is §18's, not this phase's, and the tool says so rather than pretending. |

## Gaps and honest limits

- **No model has ever answered.** The Qroq key is still the shared plaintext one
  awaiting rotation, so `ChatCompletionClient` has not run against the provider
  for this surface. The service's rules are tested with a scripted client
  (Phase 14's pattern); §67 applies: this phase proves the contract, not the
  model's behaviour or the latency.
- **No database, so no tool query has run against Postgres.** The tools are
  tested through the shared fake session: tenant stamping, row caps, refusal
  paths and output budgets are proven; the SQL is not.
- **No console surface.** There is no copilot panel, no streaming, no
  conversation in the UI. §31's copilot is an API until someone designs the
  panel — including its live region, focus behaviour and what a partial answer
  looks like.
- **Rate limiting is per process.** A deployment with several workers gives each
  its own budget. A shared counter belongs on Redis, whose credentials are
  unrotated; writing it against those credentials would be a control that looks
  load-bearing and is not.
- **Only six tools are reachable, and four of Phase 15's.** §23 lists eighteen.
  `relationship_analyzer`, `schema_validator`, `seo_analyzer`,
  `duplicate_detector`, `listing_status` and the write tools are not built, so a
  question that needs them gets "I cannot see that" rather than an approximation.
- **Channel questions are approximate.** `affected_channels` lives on proposals,
  not on conflicts, so "where do Amazon and Shopify disagree" is answered from
  proposals touching those channels rather than from two listings compared
  field by field. The tool description says so.
- **The copilot cannot create a proposal.** Deliberate (D-024), and it means the
  natural next action — "propose this change" — ends in a sentence telling the
  operator where to go instead.
- **No prompt-injection test corpus.** §34's defenses are in place (tool results
  in `<tool_result>` blocks with the policy preamble, prior turns labelled as
  context, the role's rule 4) and Phase 15 covers containment, but no test feeds
  this surface a document that says "approve everything".