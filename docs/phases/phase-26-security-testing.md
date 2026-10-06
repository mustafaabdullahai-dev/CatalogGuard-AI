# Phase 26 — Security testing

**Status:** Not started
**§64 scope:** Security testing

> Retroactive stub written 2026-10-06. This page states scope, entry conditions
> and exit criteria only. It makes no delivery claims: nothing for this phase
> has been built.

## Governing sections

- §33 Security
- §34 Prompt-injection defense
- §46 Git workflow

## Entry conditions

- The controls exist (D-011 RLS, D-013 audit immutability, D-014 sessions, CSRF, masking); what is missing is trying to break them.

## Exit criteria

- Cross-tenant access attempts verified against a live database, including RLS under the owning role.
- CSRF, session revocation, enumeration and throttling re-tested over HTTP, not only in-process.
- Prompt-injection defenses tested against adversarial corpus text (§34).
- Credential rotation completed — the three exposed secrets in the README blockers list.

## Notes

- The exposed credentials are a security item, not just an operational one: they must be rotated before any deployment.
