# Phase 27 — Performance testing

**Status:** Complete — §57's round-trip and indexing rules measured rather than reviewed, retrieval and reconciliation profiled at corpus and catalog scale, §58's frontend metrics recorded in a browser; one chunking defect found and fixed
**§64 scope:** Performance testing
**Governing sections:** §57 performance, §58 frontend performance, §53 catalog health
**Decisions:** [D-029](../../DECISIONS.md)
**Commits:** `c0144f1`, `<docs>`

> This page replaces the 2026-10-06 stub, which said "a live database to measure
> against" and listed three things it had deferred. All three are now measured.

## What this phase measured

§57's rules were reviewable claims. Four hot paths said they were batched; the
schema carried indexes; retrieval was tuned. **None of it had been executed at a
size where the claim could fail** — the unit suite runs against a handful of
rows, where a sequential scan is the correct plan and a per-row query costs
nothing.

### Round trips: an invariant, not a count

Each hot path is counted at 25 rows and at 2,000, and the assertion is that the
counts are **equal**. An N+1 that hides at 25 rows cannot hide here — the number
is not compared against a budget, it is compared against itself at two scales.

| Path | 25 rows | 2,000 rows |
|---|---|---|
| List catalog sources (with grouped record counts) | 2 | 2 |
| Product list | 1 | 1 |
| Approval queue | 3 | 3 |
| Reconciler `_load_inputs` | 2 | 2 |

All constant. The two comments in the source claiming this — the grouped count in
`list_catalog_sources` and the two-query load in `_load_inputs` — are correct,
and are now measurements.

**The counter is itself asserted.** A budget test whose counter is broken passes
forever, so one test fails if the listener observed nothing, and another fails if
the plan parser cannot report the bad answer.

### Indexing: read from the plan

At 5,000 rows, against a real planner rather than a schema definition:

| Query | Plan | Time |
|---|---|---|
| Product by workspace + SKU | `Index Scan using ix_products_workspace_status` | 0.05 ms |
| Products by workspace, ordered | index scan | — |
| Source records joined to sources | index scan | — |
| Vector search (the statement `vector_statement` builds) | **index scan** | — |

The vector plan is EXPLAINed from the statement the application issues, not a
query invented for the test: an index nobody queries is not earning its
maintenance cost.

### Retrieval at corpus scale

400 chunked documents, three orders of magnitude past the unit tests:

| Measurement | Result |
|---|---|
| Corpus ingest | 400 documents in ~2.6 s |
| Hybrid search latency | 25–58 ms per query |
| Filtered vs unfiltered | 53 ms vs 67 ms — a filter never made a query slower, which is the evidence that §14's filters run in SQL rather than after retrieval |

### Reconciliation at catalog scale

| Measurement | Result |
|---|---|
| 500 products, 2 source records each | **3.7 s**, 1,750 conflicts found |
| `build_observations` per product | 0.2 ms |

`build_observations` re-normalizes every source record on every run. D-019 and
D-020 deferred profiling it explicitly; it is the reconciler's dominant
per-record cost, and the number says whether that is affordable. At 0.2 ms per
product it is.

## §58, measured in a browser

The built console, the real API, 405 products and 405 proposals — a full page of
both tables.

| Route | Rows rendered | DOM nodes | LCP | CLS | Browser API calls |
|---|---|---|---|---|---|
| dashboard | 7 | 294 | 288 ms | 0 | 0 |
| catalog | 50 | 650 | 148 ms | 0 | 0 |
| reconciliation | 1 | 463 | 204 ms | 0 | 0 |
| approvals | 50 | 1,781 | 176 ms | 0 | 0 |
| sandbox | 0 | 129 | 116 ms | 0 | 0 |
| compliance | 2 | 99 | 120 ms | 0 | 0 |

Two things worth reading twice:

- **Zero browser API calls.** Every read is a server component fetching with the
  forwarded session (D-023), so the browser makes no data request at all on
  navigation. That is §58's SSR requirement, satisfied structurally rather than
  by configuration.
- **50 rows, not 405.** Pagination bounds the DOM: the worst screen is the
  approval queue at 1,781 nodes with a full page of 50 proposals.

### Virtualization: measured, then declined

§58 lists "virtualization for large tables". There is none, and after measuring
I left it that way: the API caps every list at 200 rows and the console pages at
50, so the worst case measured is 1,781 DOM nodes. Virtualization would add a
dependency and a scroll-position bug surface to save a number that is already
small. **This is a decision with a number attached, and the number is the point
of the phase** — if the page cap ever moves to 500, this decision is wrong and
the measurement will show it.

Script bytes per route were 5–10 KB across navigations, but that understates a
cold first load: chunks are cached after the first route. `.next/static` holds
700 KB total, with the largest single chunk at 224 KB. A budget belongs to a
deployment (Phase 28), which is where a cold-cache measurement is meaningful.

## One real defect, found by profiling with realistic content

`ingest_document` **refused a legitimate single-sentence policy rule.**

```
"Section 3. A textile product must carry a fibre content label."
  → parsed as a heading titled with the entire sentence
  → a heading with nothing under it is deliberately dropped
  → zero chunks → "content produced no chunks"
```

The drop rule is deliberate and documented — "a chunk whose entire content is a
title matches on every other title too" — and it is right. The bug was upstream
of it: the section regex has an unbounded `(.*)`, so any line *beginning* with
"Section 3." or "§2.1" was read as a heading however sentence-like the rest was.
A one-line compliance rule — entirely ordinary content — produced no chunks at
all.

Fixed by distinguishing a title from a numbered sentence on length and terminal
punctration, so "Section 3 General safety requirements" still labels its section
while "Section 3. A textile product must carry a fibre content label." becomes
content. Both cases are asserted.

## Harness lessons

The instruments were wrong before the code was, twice:

- **The plan parser reported "not indexed" for a plan opening with `Index Scan
  using …`** — it looked for the literal substring `" Scan on "`, which that
  line does not contain. It would have failed a perfectly good schema.
- **The timing helper was never awaited**, so the filter comparison raised
  `TypeError: cannot unpack non-iterable coroutine` rather than measuring
  anything.

Both are the same failure mode Phase 25 hit with the browser harness, and the
same mitigation: assert that an instrument can report the *bad* answer before
trusting it to report the good one.

## Gaps

- **No load testing.** Concurrency, saturation and connection-pool behaviour
  under parallel load are unmeasured. That needs a load generator against a
  deployment, which is Phase 28's environment; `locust` was declined here because
  it would report on this laptop rather than on a deployed container.
- **Latency budgets are per-machine and generous.** They exist so a regression
  fails as a number. A tighter budget set belongs with real hardware.
- **`build_observations`' cost is measured, not fixed.** Re-normalizing every
  record on every run is the design — a record can change, so its normalized form
  cannot be cached without an invalidation story. At 0.2 ms per product the cost
  is affordable; the measurement is what says so.
- **The frontend metrics are one machine, one browser.** Same caveat as Phase
  25's numbers.

## Verification

| Claim | How |
|---|---|
| No N+1 on the hot paths | Statement counts at two scales, compared to each other |
| The counter works | A test fails if it observes nothing |
| Indexes are used | `EXPLAIN (ANALYZE, BUFFERS)` at 5,000 rows |
| The plan parser works | Asserted against a plan with no index, and one with |
| Retrieval scales | 400 documents, latency recorded per query |
| Filters are SQL-side | A filtered query is never slower than an unfiltered one |
| Reconciliation scales | 500 products, wall time recorded |
| §58 frontend | Six routes measured in Chrome with 405 rows behind them |
| The chunking fix | Sentence-style and title-style sections both asserted |

Backend **1265 tests**; the full run reports **1262 passed, 3 skipped** with a
live API and database, ruff and mypy clean over 123 files, frontend **93 passed**
with lint and typecheck clean.