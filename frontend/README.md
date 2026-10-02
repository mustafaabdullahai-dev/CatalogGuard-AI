# CatalogGuard frontend

Next.js 16 / React 19 shell for the reconciliation console. Three routes:

| Route         | Purpose                                                             |
| ------------- | ------------------------------------------------------------------- |
| `/`           | Operations dashboard: open conflicts, pending approvals, catalog health, agent activity |
| `/catalog`    | Catalog explorer: product rows with family, channel and conflict state |
| `/approvals`  | Approval center: one card per scored change, with the score breakdown |

## Running it

```bash
npm install
npm run dev        # http://localhost:3000
```

Without a workspace configured the app serves fixtures, clearly labelled in the
UI. Fixtures exist so the layout can be reviewed before any backend route
exists; they are not a mock of expected production values, and every number is
invented.

```bash
CATALOGGUARD_API_URL=http://localhost:8000 \
CATALOGGUARD_WORKSPACE_ID=<workspace-uuid> \
npm run dev
```

With `CATALOGGUARD_WORKSPACE_ID` set, `lib/api.ts` requests the backend instead
of the fixtures. The rewrites in `next.config.ts` forward `/api/*` to
`CATALOGGUARD_API_URL`.

## Checks

```bash
npm test        # vitest, pure helpers
npm run lint    # eslint, eslint-config-next core-web-vitals + typescript
npm run typecheck
npm run build
```

## Deliberate omissions

`lib/api.ts` and `lib/types.ts` describe endpoints that the backend does not
implement yet (`/api/v1/dashboard`, `/products`, `/approvals`,
`/health-score`, `/agent-runs`). Until they exist:

- the approval controls render disabled, because approve/reject write to live
  marketplace listings and there is nothing behind the buttons;
- catalog filtering is client-side decoration, not wired to a query;
- `npm run build` succeeds against fixtures, which is the only path that can
  run without the backend.

## Conventions

- Risk bands, colors and approval requirements come from `lib/format.ts`, and
  the thresholds there mirror the backend `RiskLevel` enum. If the two drift,
  the UI can show a change as safe while the API blocks it — `requiresApproval`
  in the tests exists to make that failure loud.
- Theme state lives in the `data-theme` attribute on `<html>`, applied by an
  inline script before first paint. Components read it with
  `useSyncExternalStore` rather than mirroring it in React state.
- Server components only. There is no client state library; the interactive
  surface so far is the theme toggle.
