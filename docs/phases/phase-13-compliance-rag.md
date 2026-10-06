# Phase 13 — Compliance RAG

**Status:** Complete offline; **never run against a database or a model**
**§64 scope:** Compliance RAG
**Decisions:** [D-021](../../DECISIONS.md) compliance capability gate,
[D-006](../../DECISIONS.md) pgvector unconfirmed, [D-007](../../DECISIONS.md) LLM provider,
[D-010](../../DECISIONS.md) observability
**Commits:** `7b3bc62` (mypy cleanup), `adabaf0` (the feature), `9e3483c` (gate settled as `write`)

> **Scope conflict, unresolved.** §64 schedules this phase inside the MVP
> build order; §71 lists compliance RAG as **post-MVP** in its priority table.
> It was built in §64 order because §64 is the ordering authority the project
> follows phase by phase. If §71 was meant to win, this phase is ahead of the
> agreed scope and should be re-cut deliberately rather than left ambiguous.

## What this phase delivered

A corpus of marketplace policy that can be searched and **cited** — §13 requires
a recommendation to point at the exact rule supporting it.

- **Storage that is idempotent on content hash.** Re-posting an identical
  document returns 201 with `duplicate=true` and writes nothing, so a scheduled
  refetch of every marketplace's rules cannot stack copies until a duplicate
  outvotes the sources that agree with it.
- **Deterministic chunking with section references** (`app/rag/chunking.py`):
  headings are preserved as citations *and* as searchable text, paragraphs are
  not torn, overlap is carried across every boundary but budgeted so carrying
  context can never be what makes a chunk too large.
- **Hybrid retrieval with the filters pushed into SQL.** One keyword pass
  (`websearch_to_tsquery`, so a query cannot inject tsquery syntax) and one
  vector pass (cosine distance) both scoped to the workspace, both filtered on
  marketplace / country / document kind / rule kind / version / category path /
  effective date, unioned and re-ranked. `effective_on` keeps undated documents:
  no date means undated policy, and hiding it would remove a rule the reviewer
  needs.
- **Citations and signals in the response.** Every hit names its document,
  section reference and marketplace, and the response states which signals
  produced the ranking — because a keyword match and a semantic match are
  different kinds of evidence and §37's warning applies to both.
- **An embedding port that defaults to no vectors rather than fake ones.**
  `NullEmbedder` is the honest default; the Qroq adapter exists but has never
  been called.
- **Provider failures surface as 503, not 500** — an upstream that did not
  answer is a dependency being unavailable, and the ingest response states that
  nothing was written (which is checkable: the service embeds before it adds a
  row, and the route has not committed).

## Where it lives

- `app/rag/chunking.py`, `app/rag/embeddings.py`, `app/rag/retrieval.py`,
  `app/rag/repositories.py`, `app/rag/service.py`
- `app/api/v1/compliance.py`, `app/schemas/compliance.py`
- `app/api/deps.py` — `require_compliance_authority`
- `app/models/platform.py` — `compliance_documents`, `compliance_chunks`

## Verification

| Check | Result |
|---|---|
| `pytest` | **764 passed** (58 RAG, 14 compliance API) |
| `ruff check app tests alembic scripts` | clean |
| `mypy app` | clean, 85 files |

The SQL passes are **compiled and asserted against, not executed**: `to_tsvector`
and `cosine_distance` need live Postgres with pgvector, and D-006 records that
pgvector is still unconfirmed on Neon. Asserting on compiled SQL catches the
failure this phase *can* catch — a filter that silently does not appear in the
WHERE clause, or a query that forgot to be tenant-scoped.

## Post-phase review (fixed in this phase's final commit)

Reviewing the delivered code found five things a green test suite did not say:

1. **Section titles were not in the indexed text.** `section_reference` is a
   citation label; `search_vector` is built from `content` alone — so the
   heading line, dropped at split time, was unfindable by the query most likely
   to be asked about it. The heading now travels inside the chunk (a bare
   heading still produces no chunk: its whole content would be a title that
   matches every other title).
2. **The carried overlap could push a chunk past `max_chars`.** The tail was
   prepended without a budget, so a unit that just barely did not fit was
   re-issued oversized — retrieval truncates that silently, on precisely the
   rule the overlap existed to protect.
3. **One long paragraph became one chunk per sentence** — dozens of ~33-char
   chunks that rank on their own and hold no rule. Oversized units are now
   re-packed to the bound instead of emitted per sentence.
4. **No contract check on what a provider returned.** A wrong count misaligns
   every vector with its chunk (an off-by-one nobody can see); a wrong width
   fails far away at insert. `embed_if_available` now rejects both.
5. **One request carried the whole document**, which fails on the provider's
   input limit and takes the ingest with it. Requests are batched.

Two of the tests above were *changed* rather than added, because they pinned the
old behaviour: `test_a_heading_with_no_body_is_dropped` and
`test_split_sections_keeps_preamble_content`. Both now expect the heading in the
content, and each says why.

## Gaps and honest limits

- **Nothing here has touched a database.** No chunk row has been written to
  Neon, `to_tsvector` has never run, the vector pass has never executed.
- **pgvector is unconfirmed** (D-006). The `Vector(1536)` column and its HNSW
  index are generated, not proven to exist.
- **No embedding call has ever succeeded.** The Qroq key in `.env.example` is a
  placeholder, and probing `/embeddings` with a live key returned
  `model_not_found` for every candidate id — so as of `504009f`,
  `qroq_embedding_model` defaults to empty and `RAG_EMBEDDING_MODE=qroq` is
  refused at startup instead of failing at the first ingest. Retrieval runs
  keyword-only until a provider that serves embeddings is configured.
- **Migration index bug (found and fixed in `adabaf0`).** The generator dropped
  `postgresql_using`, so the GIN and HNSW indexes were being emitted as btree and
  would have failed at `alembic upgrade`. A mutation-checked regression test now
  pins it.
- **Reads are gated on `write`, not a read capability.** One gate per router is
  the codebase's convention; the reasoning lives on `require_compliance_authority`.
- **No compliance decision is made anywhere.** §16 and §5 make risk and
  validity deterministic, and D-005 rules out a rule-cited compliance engine in
  the MVP: these routes return evidence a later phase will consume, and the UI
  must label early compliance findings as heuristic.
