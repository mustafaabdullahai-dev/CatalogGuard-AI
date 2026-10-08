# Phase 21 — Frontend

**Status:** Complete for the API routes that exist — six of §30's nine pages, the canvas, light mode and the accessibility pass
**§64 scope:** Frontend
**Governing sections:** §29 frontend, §30 core pages, §58 frontend performance, §59 UI details, §60 design system, §61 accessibility
**Decisions:** [D-023](../../DECISIONS.md) (forwarded session, stated fallbacks, URL filters)
**Commit:** `870fcf3`

> This page replaces the retroactive stub written on 2026-10-06. Everything below
> is what this phase delivered and what a test, a build or a browser run
> confirms; the gap list is part of the delivery, not an afterthought.

## What this phase delivered

The backend grew routes through Phases 7–20 and the console did not notice: the
dashboard, catalog explorer and approval center rendered fixtures, the approve
buttons were disabled decoration, and the filters were a styled input that
filtered nothing. This phase put the two together.

- **Reads that work.** `lib/api.ts` resolves the session once per render,
  forwards the cookies the browser sent, and adds `X-Workspace-Id` from the
  caller's own memberships. Before this, every tenant route answered 401 — the
  old client passed an environment variable and no cookie, so the console could
  only ever show fixtures.
- **Writes that work, through the same origin.** Approve, approve-with-edits,
  reject, request re-analysis, approve-selected, reject-selected and
  approve-all-LOW call the Phase 18 routes from the browser through the existing
  `/api/*` rewrite, carrying the CSRF token from `GET /auth/csrf` and the header
  name that endpoint reports. Each decision answers with the backend's own
  `detail` and the audit id it wrote.
- **A refusal is not a failure.** A proposal the sandbox has not validated
  cannot be approved and the Approve button says so on the card; a CRITICAL item
  shows that it still needs a second approver; a refused bulk call reports
  "N done, M refused" rather than a spinner that stops.
- **Three new pages.** `/reconciliation` draws the Source → Truth → Analysis
  canvas with per-attribute provenance assembled from the conflicts and
  proposals the API already returns; `/sandbox` runs §18's chain and shows its
  stages, the §19 diff and the impact report; `/compliance` searches the corpus
  with the citation attached to every hit.
- **Honest numbers.** There is no `/dashboard` route, so the tiles read the
  `total` of five list endpoints fetched with `limit=1` and SQL counting. A
  number nobody can report renders as "not reported — no route", never as `0`.
- **Honest filters.** Filters are GET forms over the query string, so a filtered
  view is a URL that works without JavaScript. `GET /products` has no query
  parameters, so the catalog's text and status filters apply to the loaded page —
  and both the filter bar and the pagination line say so, the latter reporting
  "6 of the 25 products on this page · 37 in the workspace" instead of a range
  that describes rows nobody can see.
- **Fixtures that announce themselves.** Every screen shows *why* it is
  rendering invented data (not signed in, backend unreachable, workspace
  refused, server error, forced) rather than an empty table that reads as a
  clean catalog.
- **Light mode and the §61 pass.** Skip link to `<main>`, `aria-current` on the
  nav, real `<label>`s on every filter, native `<dialog>` for decisions (focus
  trap, Escape, `aria-modal`), live regions for action results, `not run` for
  every stage that could not answer, focus rings on everything focusable, and a
  contrast pass that added `--band-*` text tokens after Lighthouse measured the
  risk badges at 1.9:1–3.1:1 on their own tints.

## Where it lives

- `lib/api.ts` — server-side reads: session, workspace, pagination, stated fallbacks
- `lib/writeClient.ts` — browser-side writes: CSRF, workspace, refusal wording
- `lib/types.ts` — hand-written mirror of the backend response models
- `lib/pagination.ts`, `lib/provenance.ts`, `lib/diff.ts`, `lib/notices.ts` — pure
  modules the pages and the tests share
- `components/ApprovalQueue.tsx`, `components/SandboxPanel.tsx`,
  `components/ComplianceSearch.tsx`, `components/ReconciliationCanvas.tsx`,
  `components/DiffTable.tsx`, `components/Pagination.tsx`,
  `components/FilterBar.tsx`, `components/StateBlocks.tsx`,
  `components/NavItems.tsx`, `components/nav.ts`
- `app/{page.tsx,catalog,approvals,reconciliation,sandbox,compliance}/page.tsx`
- `app/globals.css` — tokens for both themes, the canvas, dialogs, pagination
- `tests/` — 79 vitest tests over the pure modules, the fixtures and the write client

## Verification

| Check | Result |
|---|---|
| `npm test` | **79 passed** (was 17) |
| `npm run lint` | clean (0 errors, 0 warnings) |
| `npm run typecheck` | clean |
| `npm run build` | succeeds; 6 dynamic routes, 3 static auth routes |
| Lighthouse (dashboard, approvals, reconciliation) | **Accessibility 100, Best Practices 100, SEO 100** in both themes |
| Browser (Chrome DevTools, desktop 1440×1000 and mobile 390×844) | all six pages, both themes, no console errors |

Browser verification ran against a throwaway stub of the API (see *Gaps*): it
answered the same routes with the same field names so the console could be
driven for real — filters, pagination, a full approval, a rejection with a note,
the evidence dialog, a sandbox run, a compliance search, keyboard navigation
and the mobile layout. Every write carried `X-CSRF-Token` and
`X-Workspace-Id`, and each decision visibly removed its row from the queue.

## Bugs this phase's own verification caught

Recorded because they are the argument for verifying in a browser at all:

- The sidebar rendered all six nav links **twice** (once per group) — visible in
  the accessibility tree, invisible in the code.
- `disabled` buttons had no styling, so the fixture-mode approval controls
  looked clickable.
- Every fixture id began `00000000`, so a screen that truncates ids showed the
  same prefix on every row.
- The pagination status claimed "1–25 of 37" while six filtered rows were on
  screen.
- Pagination added a `limit` parameter that no page reads.
- The reconciliation truth column rendered `title` twice — once from the catalog
  attributes, once from the resolved conflict — which reads as two truths.
- "1 conflict(s)", "2 document(s)", "1 on this page need": counts that had not
  been read by anyone.
- Light-mode badges at 2.23:1 (LOW) and 1.92:1 (MEDIUM); dark-mode badges at
  3.55:1 (CRITICAL); `--text-subtle` at 2.3:1 on light and 3.86:1 on dark.
- The logo is 3:2 and was being rendered in a square box.

## Exit criteria

| Criterion | Status |
|---|---|
| Core pages from §30: dashboard, catalog explorer, reconciliation workspace, sandbox, approval center, compliance center, agent activity | **Six of nine met.** Product Detail, Marketplace Connections and Agent Activity are not built: no route serves a single product, a connection or an agent run. Agent activity and marketplace status appear on the dashboard as labelled fixtures or an explicit "no route" card. |
| The three-panel canvas (Source → Truth → Analysis) and the per-attribute provenance flow | **Met** — `/reconciliation` draws one row per product with a disagreement: ranked source claims on the left, the authority-resolved value in the middle, the risk-scored proposal or the tie on the right. A tie is rendered as a tie. |
| Light mode and the mobile mitigation D-001 accepted as risk | **Met** — both themes ship from one token set; verified at 390×844 where the sidebar stacks and tables scroll. D-001's mobile mitigation remains an accepted risk, not a solved problem. |
| Accessibility per §61 | **Met for what is built** — keyboard navigation with a skip link, semantic HTML and landmarks, focus states, contrast measured (Lighthouse 100), native dialogs, labelled forms, live regions. No screen-reader pass has been done with an actual screen reader, which is a different claim from an automated audit. |

## Gaps and honest limits

- **Never run against the real backend.** D-006 still records the database as
  unconfirmed, so the browser verification used a throwaway stub that answers the
  same routes with the same field names. That proves the frontend's contract —
  that it sends the right headers, reads the right shapes, and degrades
  honestly — and proves nothing about the backend's behaviour. It is also not
  in the repository, so nobody can reproduce it from the tree.
- **Three §30 pages are missing**, for the reason above. They are not stubbed:
  a page that could only render invented data is worse than a card that says
  which route is missing.
- **No live marketplace anywhere in the console.** Marketplace status is a
  "no route" card, and the execution engine (Phase 20) still has no HTTP route
  of its own, so nothing in the UI can start an execution.
- **The catalog's search is page-local.** `GET /products` accepts `limit` and
  `offset` only; `q` and `status` are applied to the loaded page and labelled as
  such. A workspace-wide search needs a backend parameter.
- **Composites do not exist.** Catalog health, agent activity and marketplace
  status are fixtures because no route computes them; the dashboard says so
  beside each one.
- **No virtualisation.** Tables paginate at 25–200 rows per page, which is
  §58's pagination without its virtualisation. A workspace with 50,000 products
  pages 50 at a time, which is slow to walk and honest about where the data is.
- **No component tests.** The suite covers pure modules, the fixtures' shape and
  the write client's headers; the dialogs and the canvas are verified by browser
  runs and by `tsc`, not by rendering assertions. Installing
  `@testing-library/react` was out of scope for this phase.
- **Contrast is audited, not designed around.** Lighthouse's automated checks
  pass on three pages; the other three were checked by hand against the same
  token set.