# Phase 18 — Approval workflow

**Status:** Not started
**§64 scope:** Approval workflow

> Retroactive stub written 2026-10-06. This page states scope, entry conditions
> and exit criteria only. It makes no delivery claims: nothing for this phase
> has been built.

## Governing sections

- §15 Change proposal engine
- §20 Approval center
- §38 Human-in-the-loop
- §39 Auditability

## Entry conditions

- Proposals exist at `PENDING_APPROVAL` with a risk level and no approver assigned (D-020).
- `approval_requests` and `approval_actions` tables exist and are never written.
- `require_approval_authority` already gates the route that will approve.

## Exit criteria

- Risk level decides how many approvers, never whether one exists (D-020).
- The integration admin can never approve (D-004) — enforced, not intended.
- Every approval writes an audit row (D-013) that the database will not let anyone edit.

## Notes

- `Phase 18` is the first phase where the separation D-003/D-004 describes becomes user-visible.
