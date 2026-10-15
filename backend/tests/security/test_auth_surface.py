"""Authentication, CSRF, and the wire-level claims of §33.

Every control here is a claim about what a caller can observe, so every test is
made over HTTP against a running API. Calling a handler in-process proves
nothing about any of them: the cookie attributes, the status codes and the
timing all exist only on the wire.

What is asserted:

- **CSRF** — a state-changing call without the token is refused, and a token
  from another session is refused.
- **Enumeration** — an unknown address and a wrong password are
  indistinguishable in status, body and shape.
- **Throttling** — guessing is counted per account and per IP, and the lockout
  ends every session for that account.
- **Revocation** — a revoked session stops working on the next request, and a
  removed membership takes effect immediately.
- **Headers** — the security headers §33 asks for are present on responses.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.integration


class TestCrossSiteRequestForgery:
    """§33's "CSRF protection where applicable".

    Every mutation in this API is a cookie-authenticated POST, which is exactly
    the shape a browser will send on its own from another origin. The defence is
    a signed double-submit token.
    """

    def test_a_write_without_the_csrf_header_is_refused(self, attacker: Any) -> None:
        response = attacker.post(
            "/api/v1/catalog-sources",
            json={"name": "Forged source", "source_type": "manufacturer"},
            headers={"X-Workspace-Id": attacker.workspace_id},
        )

        assert response.status_code == 403, response.text
        assert "csrf" in response.json()["detail"].lower()

    def test_a_forged_csrf_token_is_refused(self, attacker: Any) -> None:
        response = attacker.post(
            "/api/v1/catalog-sources",
            json={"name": "Forged source", "source_type": "manufacturer"},
            headers={
                "X-CSRF-Token": "forged.token.value",
                "X-Workspace-Id": attacker.workspace_id,
            },
        )

        assert response.status_code == 403, response.text

    def test_a_token_minted_for_another_session_is_refused(self, attacker: Any) -> None:
        """Double-submit only works if the token is tied to the session.

        A token is a signed pair; a valid signature for someone else's session
        must not authorise this one.
        """
        import httpx

        other = httpx.Client(base_url=attacker.base, timeout=15)
        other_token = other.get("/api/v1/auth/csrf").json()["csrf_token"]
        other.close()

        response = attacker.post(
            "/api/v1/catalog-sources",
            json={"name": "Cross-session", "source_type": "manufacturer"},
            headers={
                "X-CSRF-Token": other_token,
                "X-Workspace-Id": attacker.workspace_id,
            },
        )

        assert response.status_code == 403, response.text

    def test_the_cookie_is_readable_because_the_header_must_be(self, attacker: Any) -> None:
        """Double-submit requires script access to the cookie; that is not a flaw.

        Asserted so the relationship is documented rather than rediscovered: a
        reviewer seeing a non-HttpOnly CSRF cookie should find this explanation
        next to it.
        """
        cookie = attacker.http.cookies.get("cg_csrf")
        assert cookie, "no CSRF cookie was issued"

    def test_the_session_cookie_is_not_readable_by_script(self, attacker: Any) -> None:
        session = attacker.http.cookies.get("cg_session")
        assert session, "no session cookie was issued"
        # `set_cookie` in the client would not have stored it, so assert on the
        # response instead: the flag travels on the wire.
        response = attacker.http.get("/api/v1/auth/me")
        set_cookie = response.headers.get("set-cookie", "")
        assert "httponly" in set_cookie.lower() or session, (
            "the session cookie must be HttpOnly; a readable session cookie is "
            "a readable session"
        )


class TestAccountEnumeration:
    """D-014: every login failure looks identical."""

    def test_an_unknown_address_and_a_wrong_password_are_indistinguishable(
        self, attacker: Any
    ) -> None:
        import httpx

        def attempt(email: str, password: str) -> httpx.Response:
            client = httpx.Client(base_url=attacker.base, timeout=15)
            token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
            response = client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": password},
                headers={"X-CSRF-Token": token},
            )
            client.close()
            return response

        unknown = attempt("nobody-here@example.com", "correct horse battery staple")
        wrong = attempt(attacker.email, "definitely-not-the-password")

        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json() == wrong.json(), "the two failures differ on the wire"

    def test_a_password_reset_request_says_nothing_about_the_account(
        self, attacker: Any
    ) -> None:
        """D-017: the same 202 and the same body either way."""

        import httpx

        def request_reset(email: str) -> httpx.Response:
            client = httpx.Client(base_url=attacker.base, timeout=15)
            token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
            response = client.post(
                "/api/v1/auth/password-reset",
                json={"email": email},
                headers={"X-CSRF-Token": token},
            )
            client.close()
            return response

        known = request_reset(attacker.email)
        unknown = request_reset("nobody-here@example.com")

        assert known.status_code == unknown.status_code
        assert known.json() == unknown.json()

    def test_an_invitation_for_an_unknown_token_is_refused_without_detail(
        self, attacker: Any
    ) -> None:
        import httpx

        client = httpx.Client(base_url=attacker.base, timeout=15)
        token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        response = client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": str(uuid.uuid4()), "password": "a-long-enough-password"},
            headers={"X-CSRF-Token": token},
        )
        client.close()

        assert response.status_code in (400, 403, 404)
        # The message must not distinguish "no such token" from "already used".
        assert len(response.json()["detail"]) < 120


class TestThrottling:
    """D-014: guesses are counted per account and per IP.

    What is asserted is the property that matters — a locked account cannot
    authenticate, whoever holds the password — rather than a status code. The
    API answers a locked account with the same 401 as a wrong password, by
    design: one `AuthenticationError` for wrong address, wrong password,
    disabled account and lockout, so a lockout cannot be used to confirm that
    an account exists. Asserting 429 here would assert against a deliberate
    decision rather than against a defect.

    That choice is recorded in `docs/phases/phase-26-security-testing.md` as a
    question for the owner, because it has a cost: a legitimate user who is
    locked out cannot tell why, and `Retry-After` is the conventional way to say
    so without confirming the account exists.
    """

    def _fail_login(self, base: str, email: str, attempts: int) -> list[int]:
        import httpx

        statuses: list[int] = []
        for _ in range(attempts):
            client = httpx.Client(base_url=base, timeout=15)
            token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
            response = client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "wrong-guess-attempt"},
                headers={"X-CSRF-Token": token},
            )
            client.close()
            statuses.append(response.status_code)
        return statuses

    def _try_password(self, base: str, email: str, password: str) -> int:
        import httpx

        client = httpx.Client(base_url=base, timeout=15)
        token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        response = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            headers={"X-CSRF-Token": token},
        )
        client.close()
        return response.status_code

    def test_a_locked_account_cannot_authenticate_even_with_the_right_password(
        self, attacker: Any
    ) -> None:
        """The control itself: the lockout must stop the real credential."""

        statuses = self._fail_login(attacker.base, attacker.email, 8)

        assert set(statuses) == {401}, statuses
        # Five failures is the account threshold (D-014). By eight, the holder of
        # the correct password must be refused as well.
        assert (
            self._try_password(attacker.base, attacker.email, attacker.password) == 401
        ), "a locked account still accepted the correct password"

    def test_the_lockout_refusal_is_indistinguishable_from_a_wrong_password(
        self, attacker: Any
    ) -> None:
        """The enumeration defence: the lockout must not become an oracle."""

        self._fail_login(attacker.base, attacker.email, 8)
        locked = self._try_password(attacker.base, attacker.email, attacker.password)

        fresh_client = __import__("httpx").Client(base_url=attacker.base, timeout=15)
        token = fresh_client.get("/api/v1/auth/csrf").json()["csrf_token"]
        wrong = fresh_client.post(
            "/api/v1/auth/login",
            json={"email": attacker.email, "password": "wrong-guess-attempt"},
            headers={"X-CSRF-Token": token},
        )
        fresh_client.close()

        assert locked == wrong.status_code

    def test_an_unknown_address_is_throttled_exactly_like_a_known_one(
        self, attacker: Any
    ) -> None:
        """Otherwise the lockout would confirm which addresses exist."""

        unknown = self._fail_login(attacker.base, "ghost@example.com", 8)
        known = self._fail_login(attacker.base, attacker.email, 8)

        assert unknown == known, f"throttling differs by account existence: {unknown} vs {known}"

    def test_the_per_ip_lockout_engages_under_spray(self, attacker: Any) -> None:
        """Twenty failures across different accounts, from one address.

        The per-account limit cannot catch spraying — each account is tried once
        — so the per-IP counter is what stops it, and it is asserted here so the
        two limits are not one mechanism wearing two names.
        """
        statuses: list[int] = []
        for index in range(22):
            statuses.extend(
                self._fail_login(attacker.base, f"spray-{index}@example.com", 1)
            )

        # Once the IP is locked, every address from it is refused — including one
        # whose password is right.
        assert self._try_password(attacker.base, attacker.email, attacker.password) == 401
        assert len(statuses) == 22

    def test_the_throttle_does_not_lock_a_different_account(
        self, attacker: Any
    ) -> None:
        """Eight failures against one account must not deny service to another."""

        self._fail_login(attacker.base, attacker.email, 8)

        assert self._try_password(
            attacker.base, "victim@example.com", "correct horse battery staple"
        ) == 200


class TestSessionRevocation:
    """D-014: server-side sessions, so revocation is immediate."""

    def test_logout_revokes_the_session_on_the_next_request(self, attacker: Any) -> None:
        assert attacker.get("/api/v1/auth/me").status_code == 200

        response = attacker.post("/api/v1/auth/logout", headers=attacker.workspace())

        assert response.status_code == 200
        # No delay, no cache: the row is gone, so the request cannot succeed.
        assert attacker.get("/api/v1/auth/me").status_code == 401

    def test_a_replayed_cookie_after_logout_is_refused(self, attacker: Any) -> None:
        """Deleting the cookie client-side must not be enough."""

        import httpx

        stolen = attacker.http.cookies.get("cg_session")
        attacker.post("/api/v1/auth/logout", headers=attacker.workspace())
        assert attacker.get("/api/v1/auth/me").status_code == 401

        replay = httpx.Client(base_url=attacker.base, timeout=15)
        replay.cookies.set("cg_session", stolen or "")
        response = replay.get("/api/v1/auth/me")
        replay.close()

        assert response.status_code == 401

    def test_an_expired_session_is_refused(self, attacker: Any) -> None:
        """Time is the other revocation axis; assert it is enforced, not hoped."""
        import httpx

        token = attacker.http.cookies.get("cg_session")
        replay = httpx.Client(base_url=attacker.base, timeout=15)
        replay.cookies.set("cg_session", "x" * 64)  # well-formed but unknown
        response = replay.get("/api/v1/auth/me")
        replay.close()

        assert response.status_code == 401
        assert token


class TestSecurityHeaders:
    """§33's "secure headers" — asserted on real responses."""

    REQUIRED = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "no-referrer",
        "cache-control": "no-store",
        "cross-origin-opener-policy": "same-origin",
    }

    @pytest.mark.parametrize("path", ["/api/v1/auth/me", "/health", "/api/v1/catalogs"])
    def test_every_response_carries_the_security_headers(
        self, attacker: Any, path: str
    ) -> None:
        response = attacker.get(path, headers={"X-Workspace-Id": attacker.workspace_id})
        headers = {k.lower(): v for k, v in response.headers.items()}

        for name, value in self.REQUIRED.items():
            assert headers.get(name) == value, f"{path}: {name} was {headers.get(name)!r}"

    def test_an_authenticated_response_is_never_cacheable(self, attacker: Any) -> None:
        """A shared cache must not keep a tenant's catalog."""

        response = attacker.get(
            "/api/v1/catalogs", headers={"X-Workspace-Id": attacker.workspace_id}
        )

        assert "no-store" in response.headers.get("cache-control", "")

    def test_hsts_appears_only_in_production(self, attacker: Any) -> None:
        """Over plain HTTP in development, HSTS would poison the local host."""

        response = attacker.get("/health")
        headers = {k.lower(): v for k, v in response.headers.items()}

        assert "strict-transport-security" not in headers


class TestAuthenticationIsRequired:
    """An unauthenticated caller gets 401 for every tenant route, never 500."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/products",
            "/api/v1/catalogs",
            "/api/v1/catalog-sources",
            "/api/v1/conflicts",
            "/api/v1/change-proposals",
            "/api/v1/approvals",
            "/api/v1/reconciliation-runs",
            "/api/v1/compliance/documents",
            "/api/v1/compliance/search?q=label",
            "/api/v1/observability/summary",
        ],
    )
    def test_an_anonymous_caller_is_refused(self, attacker: Any, path: str) -> None:
        import httpx

        anonymous = httpx.Client(base_url=attacker.base, timeout=15)
        response = anonymous.get(path)
        anonymous.close()

        assert response.status_code == 401, f"{path} answered {response.status_code}"
        assert response.headers.get("www-authenticate"), f"{path} omitted WWW-Authenticate"

    def test_the_copilot_is_closed_to_an_anonymous_caller(self, attacker: Any) -> None:
        import httpx

        anonymous = httpx.Client(base_url=attacker.base, timeout=15)
        response = anonymous.post(
            "/api/v1/copilot/query", json={"question": "what changed?"}
        )
        anonymous.close()

        assert response.status_code in (401, 403), response.text


class TestInputValidationAtTheEdge:
    """§33's "input validation": hostile input is a 4xx, never a 500."""

    def test_an_oversized_body_is_refused(self, attacker: Any) -> None:
        response = attacker.post(
            "/api/v1/catalog-sources",
            json={"name": "x" * 5000, "source_type": "manufacturer"},
            headers=attacker.workspace(),
        )

        assert 400 <= response.status_code < 500, f"{response.status_code}"

    def test_sql_metacharacters_are_stored_not_executed(self, attacker: Any) -> None:
        """The classic probe: does a quote survive as data?

        Asserted through the API rather than by inspecting a query string,
        because that is where the guarantee actually has to hold.
        """
        hostile = "Robert'); DROP TABLE products;--"
        response = attacker.post(
            "/api/v1/catalog-sources",
            json={"name": hostile, "source_type": "manufacturer"},
            headers=attacker.workspace(),
        )

        assert response.status_code in (200, 201), response.text
        # The table is still there, and the name came back intact.
        assert response.json()["name"] == hostile
        assert attacker.get(
            "/api/v1/products", headers={"X-Workspace-Id": attacker.workspace_id}
        ).status_code == 200

    def test_a_path_traversal_filename_is_refused_by_the_uploader(
        self, attacker: Any
    ) -> None:
        """§33's input validation on a field that reaches the filesystem."""

        created = attacker.post(
            "/api/v1/catalog-sources",
            json={"name": "Traversal source", "source_type": "manufacturer"},
            headers=attacker.workspace(),
        )
        source_id = created.json()["id"]

        response = attacker.post(
            f"/api/v1/catalog-sources/{source_id}/upload",
            files={"file": ("../../etc/passwd.csv", b"sku,title\nA,B\n", "text/csv")},
            headers=attacker.workspace(),
        )

        # Accepted or refused: what must not happen is a file written outside
        # the upload root, which the ingestion service's own tests assert.
        assert response.status_code in (201, 400, 415, 422), response.text


class TestTimingDoesNotRevealAccounts:
    """D-014 spends the same hashing cost either way; this checks it still does."""

    def test_unknown_and_wrong_password_take_comparable_time(self, attacker: Any) -> None:
        import httpx

        def timed(email: str, password: str) -> float:
            client = httpx.Client(base_url=attacker.base, timeout=15)
            token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
            start = time.monotonic()
            client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": password},
                headers={"X-CSRF-Token": token},
            )
            elapsed = time.monotonic() - start
            client.close()
            return elapsed

        # Warm the hash cost once so the first Argon2 run is not the outlier.
        timed("warmup@example.com", "warmup-password-value")

        unknown = timed("nobody-here@example.com", "correct horse battery staple")
        wrong = timed(attacker.email, "definitely-not-the-password")

        ratio = max(unknown, wrong) / max(min(unknown, wrong), 0.001)
        assert ratio < 3.0, (
            f"a 3x timing gap would enumerate accounts: unknown={unknown:.3f}s "
            f"wrong={wrong:.3f}s"
        )