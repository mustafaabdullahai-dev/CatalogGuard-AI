# Phase 08 — Backend foundation

**Status:** Complete
**§64 scope:** Backend foundation
**Decisions:** — (the shell; D-014's session model is [Phase 9](phase-09-authentication-authorization.md))
**Commit:** `0a4d3e7`

## What this phase delivered

The ASGI factory and everything that has to exist before a route can be
written:

- **`app.main.create_app`** — FastAPI app with `/docs`, `/openapi.json`, CORS
  from config, static mount, and seven routers included under `settings.api_prefix`.
- **`/health`** — liveness from process state alone. It deliberately does not
  probe Postgres: a database blip would otherwise take the whole service out of
  the load balancer.
- **`/ready`** — readiness that *reports rather than raises*: Postgres and
  pgvector gate real traffic, Redis does not (the API can enqueue intent
  without a live worker), so the response is `ok` / `degraded` / `unavailable`
  with per-check detail instead of a 500.
- **`SecurityHeadersMiddleware`** — `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Cache-Control: no-store`, `Cross-Origin-Opener-Policy`,
  added last so it runs first.
- **`CSRFMiddleware`** — signed double-submit (cookie + `X-CSRF-Token`),
  `Secure` + `SameSite=Lax`, safe methods and `/health` `/ready` exempt.
- **Structured logging with edge masking** (D-010) — configured key names and
  secret-shaped values redacted in the structlog processor chain, covering
  traces, errors and exception context.
- **Brand routes** — `/api/v1/brand/logo.png`, `/favicon.ico` and
  `/static/logo.png` all serve the same bytes as `frontend/public/logo.png`,
  asserted by hash.

## Where it lives

- `app/main.py`, `app/core/{config,logging,middleware}.py`, `app/static/`

## Verification

| Check | Tests |
|---|---|
| Routes in-process over ASGI, logo byte-identity, `/health` isolation | `test_http_surface.py` — 10 |
| Settings and fail-fast validators | `test_config.py` — 18 |
| Edge masking | `test_masking.py` — 15 |
| CSRF middleware behaviour | `test_csrf.py` — 21 |

## Gaps and honest limits

- No OpenAPI contract test (schema drift is unchecked) and no response-schema
  snapshot.
- `/ready` reports `redis: unverified` by design — nothing has ever probed it
  from this endpoint.
- CORS is permissive (`allow_methods=["*"]`, `allow_headers=["*"]`) with
  credentials, constrained only by `CORS_ORIGINS`. That is correct for the
  intended split (frontend origin only) but has never been exercised from a
  browser.
