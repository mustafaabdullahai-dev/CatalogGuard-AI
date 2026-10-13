# Phase 25 — Browser DevTools validation

**Status:** Complete — six screens driven against the real API and a real Postgres with the console, network, cookie, storage and performance panels open; four defects found and fixed
**§64 scope:** Browser DevTools validation
**Governing sections:** §41 browser DevTools validation, §40 end-to-end tests
**Decisions:** [D-027](../../DECISIONS.md); [D-023](../../DECISIONS.md) amended (a normalisation seam between the API and the console)
**Commits:** `f243f6a`, `605f3d0`, `<docs>`

> This page replaces the 2026-10-06 stub, which said "nothing has been opened in
> a browser from this repository yet". Half of that was true and the other half
> was not: Phase 21 had driven the console — against a stub API serving the
> frontend's own fixtures. The stub is what made the difference invisible.

## What this phase delivered

The console was first driven against the console's own fixtures in Phase 21, and
Phase 24 ran the backend against a real database. Neither had ever put the two
together. So each half was verified and the seam between them was not, and four
defects lived in that seam.

- **Signing in twice broke every request.** `get_auth_context` joined
  `auth_sessions` onto a user lookup already constrained to the session's own
  user — redundant, and multiplicative. Two sessions matched two rows and
  `scalar_one_or_none` raised `MultipleResultsFound`, so every authenticated
  request answered 500. Anyone using a laptop and a phone got a broken
  application, and the unit suite mocked the query that broke.
- **`/reconciliation` crashed the server.** Every list endpoint returns a named
  collection with no envelope (`{sources: [...]}`); the console works in
  `Page<T>` (`{items, total, limit, offset}`). `page.total` was `undefined`, and
  `plural()` calls `count.toLocaleString()`. One bad value in a supplier file
  would have done the same to a page.
- **A source has no status.** The console's badges read `status`; the API sends
  `source_type` and nothing else. It is now derived from the record count, which
  the API does send, rather than invented.
- **The console scrolled sideways on a phone.** `.stack` and `.card` are flex
  items with the default `min-width: auto`, so one wide table sized the card to
  570px inside a 390px viewport and `.table-wrap`'s `overflow-x: auto` never
  engaged.

Plus the instrument that makes it repeatable: `frontend/tests/browser/`, a
dependency-free CDP harness that opens the DevTools panels and walks the four
flows §64 names.

## Why the fixtures hid all of this

Worth stating plainly, because it is the most transferable thing here:

```
frontend/lib/fixtures.ts   ── shaped like the CONSOLE expects
frontend/lib/types.ts      ── shaped like the CONSOLE expects
backend/…/schemas/*.py     ── shaped like the API actually serves
                         ╱
              the seam ── never exercised by anything
```

Every browser run before this phase served the fixtures. Every backend test
asserted the response model the backend returns. Each side was tested against
its own idea of the other, so the disagreement was not a bug either side could
see. Phase 25's `lib/normalize.ts` is the fix, and the reason `load()` now
*requires* a normaliser rather than casting is that the cast is what let this
survive two phases.

## The instrument

`frontend/tests/browser/cdp.mjs` speaks the Chrome DevTools Protocol over a
WebSocket. No dependency: Node 22 has a global `WebSocket`, and Chrome is
already installed. Playwright would do this with more capability and more
weight — ~40 MB of browser downloads and an API surface larger than the six
screens under test. It remains the better answer if the console grows; this
file is what it would replace.

`validate.mjs` drives: sign in, every screen, three viewports, and one write
(the approve path, because a write is the only thing that proves CSRF). It
writes `report-sample.json` alongside itself.

Three harness defects were found and fixed while building it, each of which
would have produced a **false pass** — worth naming because a validator that
cannot fail is worse than none:

| Symptom | Cause | Why it mattered |
|---|---|---|
| Buttons appeared inert | React was hydrated; the detector checked `Object.keys()` for `__reactFiber$`, which is non-enumerable | It looked like a dead app and was a bug in the test |
| "Sign in" did nothing, no error | `Input.insertText` was never used; setting `.value` from script leaves React's value tracker believing nothing changed | HTML5 validation then blocked submit silently |
| A crash with no context | `waitFor` returned a DOM node with `returnByValue`; CDP rejects that | "Object reference chain is too long" names neither the page nor the wait |

## §41, item by item

| §41 check | Result |
|---|---|
| **Console** — errors, React errors, hydration, warnings | Clean on all six screens. No unhandled rejections. |
| **Network** — status, payloads, auth, CORS | Every request 2xx; the 64 aborted ones are route prefetches the console cancels on navigation, counted and classified rather than hidden |
| **Application** — cookies, storage, session | `cg_session` HttpOnly, Secure, SameSite=Lax. `cg_csrf` readable, as double-submit requires. **localStorage and sessionStorage empty** — no session in JS storage |
| **Performance** — page load, rendering, latency | LCP 108–404 ms, **CLS 0 on every screen** |
| **Responsive** — desktop, tablet, mobile | 1440 / 834 / 390 px, no horizontal overflow on any |

The four flows §64 names: **sign-in** (redirected to the dashboard, cookie
issued), **ingestion** and **reconciliation** (rows on screen, produced by the
real services from two supplier CSVs), **approval** (`POST
/api/v1/approvals/{id}/approve → 200`, verified in the database: one proposal
moved to `approved` and its `approval.approved` audit row was written).

## Gaps

- **`backend/tests/e2e` is still empty.** This phase drove a browser by hand,
  through a harness that asserts §41's panels but is not part of `npm test`
  because it needs a running stack. Making it a gate means starting the API, a
  database and the console in CI — Phase 28 has the containers that do it.
- **Dev mode does not hydrate in this environment.** `next dev` (Turbopack,
  Next 16.3.8) serves HTML that never becomes interactive, with no console
  error: React loads, the DevTools hook installs, and no renderer ever mounts.
  The production build hydrates and works. Everything here was therefore
  validated against `next start`, which is also the more honest target — it is
  what a user gets. Not diagnosed to a root cause; recorded as observed.
- **One environment trap worth writing down.** Next bakes `rewrites()` into
  `routes-manifest.json` at **build** time, so `CATALOGGUARD_API_URL` set only
  at `next start` is ignored and the proxy silently talks to the default
  `127.0.0.1:8000`. Here that was another project's server, which answered 404
  for `/api/v1/auth/csrf` — a confusing failure whose only visible symptom was
  "Could not start a session" on the sign-in screen.
- **Accessibility was not re-audited** in this phase. Phase 21 scored 100 on
  three pages with Lighthouse; the changes here were CSS `min-width` and a
  mapping layer, neither of which affects semantics, but that is an argument
  rather than a measurement.

## Verification

| Claim | How |
|---|---|
| Six screens render real data | Screenshots per screen; rows produced by the real ingestion and reconciliation services |
| Console clean | CDP `Runtime.consoleAPICalled` + `Log.entryAdded` + `exceptionThrown`, per screen |
| Session is cookie-only | `Network.getAllCookies` and `localStorage`/`sessionStorage` read after sign-in |
| Write path works | Approve clicked; request and 200 captured; database re-read |
| Responsive | `Emulation.setDeviceMetricsOverride` at three widths; scrollWidth vs clientWidth |
| Performance | `PerformanceObserver` for LCP and CLS; `Performance.getMetrics` for task and layout time |
| Harness is honest | Its three false-pass defects are listed above, each found by it failing to fail |

Backend **1169 passed, 3 skipped**; frontend **93 passed**; ruff and mypy clean
over 123 files; frontend lint, typecheck and build clean; browser run **0
failures, 0 warnings**.