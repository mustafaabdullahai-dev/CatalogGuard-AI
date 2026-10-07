# Phase 19 — Marketplace connectors

**Status:** Complete — the seam is built and proven; every live adapter stays deferred (D-005)
**§64 scope:** Marketplace connectors
**Governing sections:** §22 connector architecture, §23 tool-call architecture,
§33 security, §21 execution engine (the caller)
**Decisions:** [D-005](../../DECISIONS.md) amended 2026-10-07 (the seam, delivered)
**Commit:** `863b185`

## What this phase delivered

D-005 deferred Shopify, Amazon and TikTok Shop — but explicitly did not defer
the seam: *"The `MarketplaceConnector` abstraction and one mock adapter should
still be built during Phase 19 so the seam is proven, but with no live calls."*
That is what exists now, and nothing beyond it.

- **`app/connectors/` is the only place a marketplace is named.** One
  `MarketplaceConnector` protocol with §22's nine operations, async because
  every real adapter will be an HTTP call, plus `rollback` because §21's
  execution path needs it and the interface should be where the honesty lives.
  The core has no `if marketplace == ...` anywhere: adapters register in
  `CONNECTOR_FACTORIES` and callers ask for one by name.
- **`MockConnector` proves the seam without pretending to be a marketplace.**
  In-memory products, the full nine operations, typed answers, and a
  credential check that refuses to work without an `api_key` — so the
  "unauthenticated" branch of every path is exercised too. It deliberately
  simulates no latency, no OAuth dance and no rate-limit window: inventing
  those with no API to copy would be a guess dressed up as coverage. What it
  does offer is `fail_once`, which raises a staged error on the first attempt
  and succeeds on the second — enough for a caller to prove its retry loop
  terminates.
- **Failure is a vocabulary, not a message string.** `ConnectorError` and its
  subclasses carry `retryable`: rate limits and transients may repeat,
  auth, not-found and unsupported may not. §21's bounded-retry loop reads
  that flag instead of parsing text, which is what stops a rejected credential
  from being retried three times by a hopeful caller.
- **Credentials are encrypted by exactly one function.**
  `encrypt_credentials()`/`decrypt_credentials()` are the only path to and
  from `marketplace_connections.encrypted_credentials`, using the Fernet
  `ENCRYPTION_KEY` settings already validate at startup. A row's plaintext
  never exists outside a call frame, and `MockConnector.__repr__` does not
  include the dict it was handed.
- **`create_connection` is the first writer of the connections table.** It
  encrypts, stamps the tenant through the repository, and stores the row in
  `PENDING` — not `ACTIVE`, because nothing has told the marketplace anything
  yet. `connector_for_connection()` is the decrypt-and-dispatch step Phase 20
  will call, so one function reads secrets and no execution path stumbles into
  one by accident.
- **The registry refuses rather than guesses.** An unknown marketplace raises
  `UnknownMarketplaceError` naming what is registered and why nothing else is.
  Falling back to the mock would report successful writes against a store that
  does not exist.

## Where it lives

- `app/connectors/base.py` — protocol, data types, error vocabulary, §21's
  `supports_rollback` contract
- `app/connectors/mock.py` — `MockConnector`
- `app/connectors/credentials.py` — Fernet encrypt/decrypt, `CredentialError`
- `app/connectors/registry.py` — `CONNECTOR_FACTORIES`, `connector_for()`
- `app/services/connections.py` — `create_connection`, `credentials_for`,
  `connector_for_connection`, `ConnectionRepository`
- `tests/unit/test_connectors.py` — 32 tests

## Verification

| Check | Result |
|---|---|
| `pytest` | **929 passed, 3 skipped** (886 unit, 43 agent offline, 3 live opt-in) |
| `ruff check app tests alembic scripts` | clean |
| `mypy app` | clean, 105 files |

The conformance test reads §22's list and asserts each operation exists on
both the protocol and the adapter; a second test asserts that no module inside
`app/connectors/` is named after a marketplace D-005 defers. The §33 test
inspects the actual table columns rather than trusting the model's comment.

## Exit criteria

| Criterion | Status |
|---|---|
| `MarketplaceConnector` abstraction plus one mock adapter, so the seam is proven | **Met** — protocol with §22's nine operations + `rollback`; `MockConnector` implements all of them and satisfies `isinstance(x, MarketplaceConnector)`; 32 tests drive every operation, the auth refusal, the retryable vocabulary and the registry |
| Credentials encrypted at rest with `ENCRYPTION_KEY`, never plaintext | **Met** — one encryptor, ciphertext round-trips, `inspect(MarketplaceConnection)` shows no plaintext credential column, and the tampered-blob and wrong-key cases both fail loudly |
| No live calls in the MVP | **Met** — there is no HTTP client in `app/connectors/`; rate limits, auth quirks and partial-failure modes against a real API stay unvalidated, and are labelled as such below |

## Gaps and honest limits

- **Nothing calls the seam in production.** No route creates a connection, no
  job dispatches to one. The proof is in the tests: the execution engine
  (Phase 20) is the first caller, and until it exists this is a seam with no
  traffic. That is the state D-005 asks for — proven, unused.
- **Exactly one adapter, and it is the mock.** No Shopify, Amazon or TikTok
  connector exists. `connector_for("shopify", ...)` raises rather than
  returning something that would answer as if it had a store.
- **Real rate limits, auth expiry, pagination and partial failures are
  unvalidated.** The mock has no clock and no network, so §21's retry
  behaviour is proven against staged errors, not against a marketplace's
  actual throttling. This is the gap D-005 accepted, restated in the terms of
  what was actually built.
- **`validate_listing` speaks the mock's vocabulary.** A real adapter's issues
  come from that marketplace's own validator; the mock checks title, price and
  inventory because those are the three it can honestly judge.
- **No connection management surface.** There is no API to list, rotate or
  revoke a connection, and `ConnectionStatus` transitions are not driven by
  anything. Credentials rotate by replacing the row, which requires code.
- **`granted_scopes` is recorded, never checked.** The column stores what was
  requested so a re-auth can be judged later; nothing consults it before an
  operation, because no marketplace defines what a scope would block yet.
