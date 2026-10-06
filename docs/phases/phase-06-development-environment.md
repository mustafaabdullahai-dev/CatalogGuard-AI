# Phase 06 — Development environment

**Status:** Complete, with one real gap (no lock file)
**§64 scope:** Development environment
**Decisions:** — (§43 development environment, §44 Python environment, §45
environment variables)

## What this phase delivered

- **A working venv at `backend/.venv`** running **Python 3.12.14**;
  `requires-python = ">=3.12,<3.14"`.
- **`pyproject.toml`** carrying the application dependencies *and* the three
  gates, so every contributor runs the same checks: `ruff` (line length 100,
  `target-version = py312`), `mypy` (`strict = true`, `warn_unused_ignores`),
  `pytest` (`asyncio_mode = auto`, `testpaths`, `pythonpath = ["."]`,
  `addopts = "-q --strict-markers"`).
- **`.env.example`** with every variable documented and no secret in it;
  `.env` git-ignored and loadable from the repo root.
- **Config that fails fast** (D-009, D-007): placeholder API keys, plaintext
  connection strings and `redis://` are all rejected at startup rather than at
  first use.
- **`python -m scripts.generate_initial_migration`** — the migration is a
  build artifact regenerated from the ORM, with a test that fails if the
  committed file differs.

```bash
cd backend
source .venv/bin/activate
python -m pytest              # 781 tests, no database required
python -m ruff check app tests alembic scripts
python -m mypy app
```

## Where it lives

- `backend/pyproject.toml`, `backend/.venv/`, `.env.example`

## Verification

Verified every run: the three commands above are green as of `9e3483c`.

## Gaps and honest limits

- **Dependencies are not pinned.** Every entry is a lower bound
  (`fastapi>=0.115`, `langgraph>=0.2`) and there is **no lock file** — no
  `uv.lock`, no `requirements.txt`, no `poetry.lock`. The venv is reproducible
  only as long as nobody rebuilds it. README claimed "pinned dependencies";
  that claim was wrong and has been corrected. Freezing the environment would
  close this.
- **README claimed Python 3.13.** The venv is 3.12.14. Corrected.
- No container for development (§47 / Phase 28); `docker/` is empty.
