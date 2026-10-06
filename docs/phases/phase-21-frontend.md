# Phase 21 — Frontend

**Status:** Partially built — shell, auth flows, dashboard, catalog and approvals pages
**§64 scope:** Frontend

> Retroactive page written 2026-10-06 from the commits and the tree in
> `frontend/`. It records what exists; the exit criteria below are what
> remains, and the notes claim nothing the tree cannot show.

## Governing sections

- §29 Frontend
- §30 Core frontend pages
- §59 UI details
- §60 Design system
- §61 Accessibility

## Entry conditions

- D-001 is decided: AI Enterprise Command Center, dark-first, indigo→violet reserved for AI affordances.

## Exit criteria

- Core pages from §30: dashboard, catalog explorer, reconciliation workspace, sandbox, approval center, compliance center, agent activity.
- The three-panel canvas (Source → Truth → Analysis) and the per-attribute provenance flow.
- Light mode and the mobile mitigation D-001 accepted as risk.
- Accessibility per §61.

## Notes

- Built today: `frontend/app/` with dashboard, catalog, approvals, login, reset-password, accept-invitation.
- Open from D-001: light-mode semantic ramp for the AI accent, command palette scope, font delivery method.
