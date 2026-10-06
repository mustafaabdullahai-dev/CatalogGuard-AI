# Phase 09 — Authentication / authorization

**Status:** Complete (mail delivery unverified — outbox only)
**§64 scope:** Authentication / authorization
**Decisions:** [D-014](../../DECISIONS.md) authentication · [D-017](../../DECISIONS.md)
account recovery and invitations
**Commits:** `dd394c2` (sign-in), `107e669` (reset + invitations)

## What this phase delivered

**Sign-in (D-014).** Email and password against Argon2id, exchanged for an
opaque session cookie. The session lives in Postgres so it can be revoked; the
cookie carries a random token and the database stores only its SHA-256 digest.
No JWTs, so revocation takes effect immediately.

- **Bootstrap, not self-registration.** `python -m app.cli.bootstrap --email
  you@example.com` creates the first administrator; everyone else arrives by
  invitation.
- **Identical responses for existent and non-existent addresses**, same status,
  same body, same hashing cost — responses cannot enumerate accounts.
- **Throttled twice**, per account and per IP, because neither alone stops
  guessing and spraying.
- **`X-Workspace-Id` is a hint, not authority.** `get_current_tenant` checks it
  against the caller's memberships and binds the tenant for RLS; a workspace
  you do not belong to is 403.
- **Capabilities from one matrix** (`RoleCapabilities`), so D-003/D-004 are
  enforced in one place. The integration role can never approve.

**Recovery and invitations (D-017).** Single-use reset tokens with a bounded
live-token count, session revocation on reset (every session, not just the
requesting device), separate throttling budgets so a reset flood cannot lock
someone out of login, and invitations whose role is bound at issue — acceptance
takes no role parameter and never overwrites an existing account's password.
Both flows write to `email_outbox` and stop.

## Where it lives

- `app/api/v1/{auth,invitations}.py`, `app/api/deps.py`
- `app/services/auth/{login,sessions,password_reset,invitations,passwords}.py`
- `app/cli/bootstrap.py`, `app/cli/outbox.py`
- `frontend/app/{login,reset-password,accept-invitation}/`

## Verification

| Area | Tests |
|---|---|
| Capability matrix, tenant binding | `test_access_control.py` — 32 |
| Authorization behaviour per role | `test_authorization.py` — 15 |
| Endpoint wire contract, identical 401s | `test_auth_endpoints.py` — 15 |
| Auth services (session arithmetic, throttling) | `test_auth_services.py` — 23 |
| CSRF middleware | `test_csrf.py` — 21 |
| Password hashing policy | `test_passwords.py` — 18 |
| Reset flow | `test_password_reset.py` — 23 |
| Invitations | `test_invitations.py` — 25 |
| Reset + invitation endpoints | `test_recovery_endpoints.py` — 23 |

**195 tests**, all with a mocked database.

## Gaps and honest limits

- **No mail provider.** Both flows queue to `email_outbox` with `sent_at` NULL.
  `python -m app.cli.outbox` prints them, which is how the flows are exercised
  offline. A queued message nobody reads is a reset that never arrives, and the
  person cannot tell it from a non-existent address — the cost of the
  identical-response rule.
- **Unit-tested, not integration-tested.** The queries have never run against
  real SQL; only the hashing, session arithmetic, CSRF and authorization matrix
  are covered.
- **No MFA and no SSO.** Both would attach to the session record rather than the
  password path.
- Two indexed lookups per authenticated request (session, then memberships).
  Caching either would re-introduce the revocation problem D-014 was chosen to
  solve.
