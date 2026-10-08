# CatalogGuard frontend

Next.js 16 / React 19 console for reconciliation review. Server components read
the backend; the three write surfaces (approval decisions, sandbox simulate,
compliance search) are the only client components.

| Route              | Purpose                                                                                     |
| ------------------ | ------------------------------------------------------------------------------------------- |
| `/`                | Operations dashboard: open conflicts, pending approvals, last run, risk distribution         |
| `/approvals`       | Approval center: one card per queued change, with approve / edit / reject / re-analysis      |
| `/catalog`         | Catalog explorer: canonical products with identifiers, status, version and attribute count   |
| `/reconciliation`  | Source → Truth → Analysis canvas with per-attribute provenance and run history                |
| `/sandbox`         | Runs §18's chain on a copy and shows stages, the §19 diff and the impact report              |
| `/compliance`      | Hybrid-retrieval search with per-hit citations, plus the indexed document list                |
| `/login`, `/reset-password`, `/accept-invitation` | Account flows (unchanged since Phase 9)                          |

## Running it

```bash
npm install
npm run dev        # http://localhost:3000
```

With no session, every screen renders invented fixtures and says so in a banner
above the fold (`lib/notices.ts`). Point the app at a backend and sign in to read
a real workspace:

```bash
CATALOGGUARD_API_URL=http://127.0.0.1:8000 npm run dev
```

`lib/api.ts` forwards the incoming cookies to the backend, so the session cookie
set on this origin by the `/api/*` rewrite is the one that travels; the
workspace comes from `GET /auth/me` (a `cg_workspace` cookie overrides it).
Set `CATALOGGUARD_FIXTURES=1` to force fixtures even with a backend running.

> On this machine port 8000 is taken by an unrelated project, so the API URL
> above has to be changed. Nothing in the app assumes 8000.

## Checks

```bash
npm test           # vitest: 79 tests over the pure modules and the write client
npm run lint       # eslint, eslint-config-next core-web-vitals + typescript
npm run typecheck  # tsc --noEmit
npm run build      # production build; succeeds against fixtures and live alike
```

## How the data layer works

- **`lib/api.ts` (server only)** reads. It resolves the session once per render
  (`cache()`), forwards cookies, adds `X-Workspace-Id`, and returns
  `Loaded<T> = { data, source, reason }` — so a page can say *why* it is showing
  fixtures ("not signed in", "backend unreachable", "workspace refused") rather
  than implying an empty workspace.
- **`lib/writeClient.ts` (browser only)** writes. Mutations go through the
  same-origin `/api/*` rewrite with `credentials: "include"`, fetch the CSRF
  token from `GET /auth/csrf` and copy the header name the endpoint reports, and
  resolve the workspace once per page load. A refusal surfaces the backend's own
  `detail` string.
- **`lib/types.ts`** mirrors the backend response models by hand. The list
  endpoints all return `{ rows, total, limit, offset }`; `tests/fixtures.test.ts`
  asserts the fixtures still match those shapes so a backend rename fails a test
  rather than a screen.
- **Filters are GET forms.** `?q=&status=&page=&page_size=` — shareable,
  back-button-correct, and they work without JavaScript. `GET /products` takes
  no query parameters, so the catalog's text and status filters apply to the
  loaded page and the UI says so; the pagination status line reports rows shown
  versus rows on the page rather than pretending the filter is server-side.

## Deliberate omissions

- **No Product Detail, Marketplace Connections or Agent Activity pages.** §30
  lists nine pages; no API route serves a single product, a connection or an
  agent run, so those three are not built. The dashboard shows agent activity
  and marketplace status from labelled fixtures with an explanation instead of
  routing to a page that could only ever be invented.
- **No client state library, no data-fetching library, no UI kit.** Filters are
  URLs, mutations are one `fetch` each, and the design system is
  `app/globals.css`. Adding TanStack Query would mean the URL-driven filter
  state had two sources of truth.
- **`q` on `/catalog` is a page-local filter**, not a workspace search, because
  the route has no `q`. Wiring it properly is a backend change.
- **Bulk decisions are not optimistic.** D-020 keeps a person in the loop, and a
  queue that showed a decided item before the server agreed would be a lie.

## Conventions

- Risk bands, colors and approval requirements come from `lib/format.ts` and
  `app/globals.css`, mirroring the backend `RiskLevel` enum. Badge *text* uses
  `--band-*` tokens rather than the vivid band colour: the vivid values are
  right for a dot and unreadable as 11px text on a tint (measured 1.9:1–3.1:1
  before the tokens existed).
- `--ai` / `--ai-soft` are the only violet in the app, per D-001, and only the
  agent panel consumes them.
- Theme state lives on `data-theme` on `<html>`, applied by an inline script
  before first paint; components read it with `useSyncExternalStore`.
- A stage verdict of `null` renders as "not run", never as a pass (§18), and a
  field with two sources at the same authority rank renders as a tie rather than
  being resolved (§30 / D-020).