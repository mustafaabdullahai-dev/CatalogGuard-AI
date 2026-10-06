# Phase 01 — UI/UX discovery

**Status:** Complete (decision recorded; implementation belongs to [Phase 21](phase-21-frontend.md))
**§64 scope:** UI/UX discovery
**Decisions:** [D-001](../../DECISIONS.md)

## What this phase delivered

A single chosen direction — **AI Enterprise Command Center**, dark-first
neutral/zinc monochrome with indigo→violet reserved exclusively for AI
affordances — recorded with the palette, type scale, radius, navigation
structure, top bar, home layout and signature workspace laid out as a table
someone can build from.

Four directions were considered and three rejected in writing: a dark
futuristic console (visual noise, weak table density), an enterprise data
platform as the sole direction (functional, no AI-native identity), and a
chat-first AI-native UI (conflicts with §5 — evidence and workflow, not a
chatbot). The chosen direction is Linear/Vercel minimal carrying a
Stripe-style data layer.

The signature surfaces were specified here, not invented later: a three-panel
reconciliation canvas (Source Data → Canonical Truth → AI Analysis), a
provenance flow per attribute, and a sandbox laid out as git-diff ×
Figma-prototype (Before / Proposed / Impact plus a validation checklist).

## Where it lives

- `DECISIONS.md` — D-001, including the full options table.
- `frontend/` — the shell that implements part of it (Phase 21).

## Verification

Not a coded phase. The decision is testable in the sense that D-001 names the
tokens and components a later implementation must match; no such conformance
test exists yet.

## Gaps and honest limits

- **Three open items were deferred by design** and remain open: font delivery
  method (Phase 4), the light-mode semantic ramp for the AI accent (Phase 21),
  command palette scope (Phase 21).
- **Mobile responsiveness was accepted as risk, not solved.** D-001 records the
  architect's desktop-first recommendation was overridden; the mitigation
  (single-column collapse, card/row-detail tables below `md`, tabbed canvas) is
  specified but not built.
- Dark-first **with** light mode doubles the token and component QA surface.
  Neither theme has a visual-regression test.
