# Phase 10 — Catalog ingestion

**Status:** Complete (async worker **unverified** — no live Redis)
**§64 scope:** Catalog ingestion
**Decisions:** [D-018](../../DECISIONS.md) catalog ingestion
**Commit:** `cfe1ede`

## What this phase delivered

CSV, JSON and Excel intake that stores **raw and stores nothing else**:

- **Deterministic file validation** — extension allowlist, magic bytes, size
  cap, binary refusal, formula neutralisation, row and column caps. XML and PDF
  are excluded rather than accepted and left unread.
- **Schema detection** over the uploaded file, then `source_records` written
  as-is. Interpretation belongs to normalization
  (`app/services/catalog/normalization.py`, D-015); ingestion refuses to guess.
- **A `FileScanner` port** defaulting to an adapter that reports `none` and
  says so, rather than implying a scan happened.
- **Structured failures.** `IngestionError` carries a machine-readable code
  (`app/services/ingestion/errors.py`), so a rejected upload answers *why*
  rather than returning a 500.
- **Duplicate identity columns are suffixed, not dropped.** Two rows carrying
  the same external id both survive, disambiguated — silently keeping one would
  lose data the operator can no longer see.
- **A sync/async seam.** Files at or below `INGESTION_INLINE_MAX_BYTES` parse
  in-request; larger ones hand off to a Dramatiq worker only when
  `INGESTION_EXECUTION=dramatiq`, which defaults to inline.

## Where it lives

- `app/api/v1/ingestion.py`, `app/services/ingestion/`, `app/workers/ingestion.py`
- `app/schemas/ingestion.py`, `app/models/catalog.py` (`catalog_sources`, `source_records`)

## Verification

`tests/unit/test_ingestion.py` — **44 tests**: validation matrix, each parser,
schema detection, raw-only storage, the sync/async seam decision, and the
endpoint's 401 with an unreachable database.

## Gaps and honest limits

- **The Dramatiq path has never run.** No live Redis worker has consumed a job;
  only the inline path is exercised. `REDIS_URL` is verified as a service but
  no task has been published to it.
- **No live file has been uploaded.** Everything above is offline.
- Downloading from a URL or a marketplace feed is not part of this phase; intake
  is upload-only.
