# Phase 23 — Observability

**Status:** Complete — §55's context binding, a trace port with a self-hosted Langfuse adapter, and in-app accounting; the Langfuse wire call is unverified
**§64 scope:** Observability
**Governing sections:** §32 observability, §55 logging, §56 cost control, §39 auditability
**Decisions:** [D-010](../../DECISIONS.md) implemented (not amended); [D-025](../../DECISIONS.md) (correlation, in-app accounting, per-process limit)
**Commit:** `a0aca91`

> This page replaces the 2026-10-06 stub, which said "not started (D-010 decided
> the approach; nothing has been exported)". Both halves of that were true: the
> approach was decided and nothing was exported. Both are now false, and the
> halves are false for different reasons worth keeping apart — see *Gaps*.

## What this phase delivered

Two absences, not two half-features. `bind_context()` had existed since Phase 8
and nothing called it, so §55's `request_id`, `workspace_id` and `user_id` were
missing from every log line — the masking processor had nothing to correlate.
`langfuse_enabled`, `langfuse_host` and the two keys had existed in settings
since Phase 4 and nothing read them. §32 listed nine signals and the system
emitted none.

- **Every log line is correlatable now.** `RequestContextMiddleware` binds
  `request_id`, `method`, `path`, `status` and `duration_ms` for the life of one
  request, returns the id as `X-Request-Id`, and clears the context afterwards so
  one request cannot inherit the last one's status. `get_current_tenant` binds
  `workspace_id` and `user_id` once it has resolved them, which is the first
  point either is known. An inbound id is honoured only when it is a short opaque
  token: a log line is not a place to echo a caller's string unexamined.
- **A trace port, not a vendor.** `app/core/telemetry.py` defines `TraceSink`
  with a null adapter by default and a self-hosted Langfuse adapter behind it,
  chosen by configuration so no call site branches on "is observability enabled"
  — the same shape Phase 10's mail port and Phase 19's connector registry used,
  for the same reason: the deployment D-010 names does not exist until Phase 28.
- **Events carry counts, never content.** Every event is a kind, a name, a
  duration, a status, ids, and small numeric or boolean attributes. Content-shaped
  keys (`query`, `prompt`, `content`, `sku`, `arguments`, …) are masked at the
  event boundary even if a future caller attaches them. A compliance question is
  written from catalog content, and D-010's rule 3 applies one layer up too.
- **All nine of §32's signals have somewhere to come from.** Agent runs and tool
  calls (Phase 20's writers), LLM calls with tier/model/latency and tokens
  whatever the outcome, retrieval with hit counts and signals, executions from
  the one place every outcome passes through, failures as statuses on all four,
  workflow state as bound-context logs, and `model_usage` as the per-call model
  id.
- **In-app accounting (D-007, §56).** `GET /api/v1/observability/summary`
  aggregates the rows the system already writes: runs by status with p50/p95
  latency, token totals and repair rate; per-tool volume and failures;
  retrieval signals and empty-result rate; execution outcomes, attempts,
  rollbacks. Gated on `admin` rather than `read`, because token spend and failure
  rates are cross-cutting operational data rather than catalog data.
- **The response says what it cannot see.** A signal nobody has emitted is
  `null`, never `0`, and `unavailable` names the gap in words ("no tool calls
  recorded in this window", "tool statistics cover 2000 of 9001 calls (row cap)").
  `signals` maps each of §32's requirements to the row or log field that feeds
  it, so a reader can tell which requirement a deployment does not yet meet.

## Where it lives

- `app/core/telemetry.py` — `TelemetryEvent`, `TraceSink`, `NullTraceSink`,
  `LangfuseTraceSink`, configuration
- `app/core/middleware.py` — `RequestContextMiddleware`
- `app/api/deps.py` — workspace/user binding where they are first known
- `app/agents/tools/recording.py` — agent-run and tool-call events
- `app/agents/client.py` — LLM-call event (tier, model, latency, outcome)
- `app/rag/service.py` — retrieval event (counts and signals, never the query)
- `app/services/execution/engine.py` — execution event for every outcome
- `app/services/observability.py` — `percentile`, `summarize`, `collect`
- `app/api/v1/observability.py` — `GET /api/v1/observability/summary`
- `tests/unit/test_telemetry.py` (25), `test_observability.py` (20),
  `test_observability_summary.py` (17)

## Verification

| Check | Result |
|---|---|
| `pytest` | **1068 passed, 3 skipped** (1025 unit, 43 agent offline, 3 live opt-in) |
| `ruff check app tests alembic scripts` | clean |
| `mypy app` | clean, 120 files |

## Exit criteria

| Criterion | Status |
|---|---|
| Agent runs, latency, token usage, tool calls, failures, retrieval quality, model usage, workflow state, execution status are tracked (§32) | **Met, with one caveat stated in the response itself**: retrieval quality and model usage are traces, so they report `null` until a sink is configured; the other seven come from rows and work today. |
| Structured logs with the nine fields §55 lists | **Met** — `timestamp`, `request_id`, `workspace_id`, `user_id`, `method`, `path`, `status`, `duration_ms`, `error`, with `tool`/`operation` on the component lines that have them, and masking before serialization. |
| Cost control accounted in-app (§56, D-007) | **Partial**: tokens per run and per window are counted, split by status and run type. There is no price table, so the summary reports tokens rather than currency — D-010's "no vendor cost dashboard" is worked around, not solved. |
| D-010 respected: self-hosted Langfuse only, edge masking mandatory, no secrets in telemetry | **Met, with the wire call unverified.** Only self-hosted Langfuse is implemented; LangSmith and public aggregators remain prohibited; masking runs in the processor chain and again on event payloads; the adapter's credentials are read at emit time and appear in no event, log line or payload. |
| Nothing exported to a third party | **Met** — the sink is null by default, so a deployment with no configuration exports nothing at all. |

## Gaps and honest limits

- **The Langfuse adapter has never reached a Langfuse.** Its payload shape,
  credential handling, plaintext refusal and failure behaviour are tested through
  an injected transport; the HTTP round trip has not happened, because no
  Langfuse instance has ever been reachable and the deployment target arrives in
  Phase 28. D-010 said structured logging would carry observability until then —
  which is exactly the state this phase leaves it in.
- **Two of §32's nine signals report `null` until a sink exists.** Retrieval
  quality and model usage live in traces, not in tables. Writing them to a table
  would mean inventing an observability schema that D-010's chosen destination
  does not need, so they report their absence instead.
- **Rate limiting is per process** (Phase 22's copilot carries the same caveat).
  A shared counter belongs on Redis, whose credentials are still the originals
  awaiting rotation. This phase did not add a cross-request rate limit for
  observability itself, because the endpoint only reads workspace-scoped rows.
- **The summary reads up to 2,000 rows per section.** A busy workspace gets a
  truncated view, and the payload says so rather than presenting a partial
  percentile as a window-wide one. There is no aggregation table; Phase 24/27 may
  want one if dashboards become a feature.
- **No Prometheus endpoint and no dashboards.** §32 asks what to track, not where
  to chart it. The summary endpoint is a stable shape a dashboard could poll,
  but nothing renders it.
- **No alerting.** Nothing pages anyone when executions start failing or a model
  tier starts repairing every response. That needs a destination and a policy,
  and both belong to the deployment phase.
- **Traces are per-process and best-effort.** `urllib` in a thread-free async
  handler is fine for a fire-and-forget observation, but a burst of emissions
  will serialize behind DNS and TLS. A batching sink would be the first thing to
  add when there is a real volume to batch.
- **The middleware logs one line per request**, which at volume is most of the
  log. That is a deliberate trade: correlation is worth the bytes, and Phase 28
  decides where the bytes go.