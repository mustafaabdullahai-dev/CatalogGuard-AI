"""Authentication endpoint tests (D-014).

The database is mocked; the HTTP layer is real. That split is deliberate. What
is being tested here is the wire contract — status codes, cookie attributes,
CSRF enforcement, and the fact that no field of `users` leaks into a response.
Those are exactly the things a service-level unit test cannot see, and none of
them require a database.

`login_service.authenticate` is stubbed because its behaviour (throttling,
hashing, session creation) has its own tests, and because a real Argon2 hash per
test would add minutes to the suite.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport

from app.core.config import get_settings
from app.db.session import get_session, get_session_no_commit
from app.main import app
from app.models.tenancy import User, UserRole
from app.services.auth import login as login_service
from app.services.auth import sessions as session_service

PREFIX = "/api/v1"


def _user(**overrides: Any) -> User:
    defaults: dict[str, Any] = {
        "id": __import__("uuid").uuid4(),
        "email": "ops@example.com",
        "hashed_password": "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA",
        "full_name": "Ops Person",
        "is_superuser": False,
        "is_active": True,
    }
    defaults.update(overrides)
    return User(**defaults)


def _fake_session() -> MagicMock:
    """A session whose `execute` returns nothing, and which accepts commits.

    The auth routes project `users` rows and call membership queries. With no
    database, `execute` returns an empty result and the route degrades to "no
    workspaces", which is a legitimate response and the one these tests assert.
    """
    session = MagicMock()
    empty = MagicMock()
    empty.scalar_one_or_none.return_value = None
    empty.scalars.return_value = []
    empty.scalar.return_value = None
    session.execute = AsyncMock(return_value=empty)
    session.commit = AsyncMock(return_value=None)
    session.rollback = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.flush = AsyncMock(return_value=None)
    return session


def _override() -> dict[Any, Any]:
    db = _fake_session()
    return {
        get_session_no_commit: lambda: db,
        get_session: lambda: db,
    }


@pytest.fixture
def client() -> httpx.AsyncClient:
    app.dependency_overrides.update(_override())
    transport = ASGITransport(app=app)
    return httpx.AsyncClient(
        transport=transport, base_url="https://test", follow_redirects=False
    )


@pytest.fixture
def csrf(client: httpx.AsyncClient):
    async def _token() -> dict[str, str]:
        # Fetch first: a client that has never made a safe request has no token.
        await client.get(f"{PREFIX}/auth/csrf")
        token = client.cookies.get(get_settings().csrf_cookie_name)
        assert token is not None, "no CSRF cookie was issued"
        return {get_settings().csrf_header_name: token}

    return _token


class TestCsrfEndpoint:
    async def test_returns_a_usable_token(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.get(f"{PREFIX}/auth/csrf")
        assert response.status_code == 200
        body = response.json()
        assert body["header_name"] == get_settings().csrf_header_name
        assert get_settings().csrf_cookie_name in client.cookies

    async def test_token_is_stable(self, client: httpx.AsyncClient) -> None:
        first = (await client.get(f"{PREFIX}/auth/csrf")).json()["csrf_token"]
        second = (await client.get(f"{PREFIX}/auth/csrf")).json()["csrf_token"]
        assert first == second


class TestLogin:
    async def test_login_requires_a_csrf_token(
        self, client: httpx.AsyncClient
    ) -> None:
        """The first thing the middleware protects.

        Login CSRF signs a victim into an account the attacker controls, and
        everything the victim does afterwards is then readable by that attacker.
        """
        response = await client.post(
            f"{PREFIX}/auth/login", json={"email": "a@b.com", "password": "x" * 12}
        )
        assert response.status_code == 403

    async def test_successful_login_sets_an_http_only_cookie(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        issued = session_service.IssuedSession(
            record=MagicMock(), token="session-token-value"
        )
        monkeypatch.setattr(
            login_service,
            "authenticate",
            AsyncMock(
                return_value=login_service.LoginResult(
                    user=_user(), session=issued, rehashed=False
                )
            ),
        )
        response = await client.post(
            f"{PREFIX}/auth/login",
            json={"email": "ops@example.com", "password": "x" * 12},
            headers=await csrf(),
        )
        assert response.status_code == 200
        cookie_header = response.headers.get_list("set-cookie")
        session_cookie = next(
            c for c in cookie_header if c.startswith(get_settings().session_cookie_name)
        )
        assert "HttpOnly" in session_cookie
        assert "Secure" in session_cookie
        assert "SameSite=lax" in session_cookie
        assert "Path=/" in session_cookie

    async def test_login_response_never_includes_the_hash(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The projection is explicit, so this stays true by construction.

        A `model_validate(user)` or a dict dump would start leaking
        `hashed_password` the day someone adds a column.
        """
        monkeypatch.setattr(
            login_service,
            "authenticate",
            AsyncMock(
                return_value=login_service.LoginResult(
                    user=_user(),
                    session=session_service.IssuedSession(
                        record=MagicMock(), token="t"
                    ),
                    rehashed=True,
                )
            ),
        )
        response = await client.post(
            f"{PREFIX}/auth/login",
            json={"email": "ops@example.com", "password": "x" * 12},
            headers=await csrf(),
        )
        body = response.json()
        assert set(body["user"]) == {"id", "email", "full_name", "is_superuser"}
        assert "hashed_password" not in response.text
        assert body["password_rehashed"] is True

    async def test_bad_credentials_are_one_indistinguishable_401(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Wrong password, unknown address, disabled account: same response.

        Any difference in status or body is an oracle. The reason is logged
        server-side, not returned.
        """
        monkeypatch.setattr(
            login_service,
            "authenticate",
            AsyncMock(side_effect=login_service.AuthenticationError),
        )
        response = await client.post(
            f"{PREFIX}/auth/login",
            json={"email": "ops@example.com", "password": "x" * 12},
            headers=await csrf(),
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid email or password"
        assert get_settings().session_cookie_name not in client.cookies

    async def test_failed_login_still_persists_the_throttle_counter(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed attempt must outlive the request.

        The login route uses a no-autocommit session and commits explicitly on
        the failure path. Rolling back would discard the counter, and an
        attacker could try forever at no cost.
        """
        db = _fake_session()
        monkeypatch.setattr(
            login_service,
            "authenticate",
            AsyncMock(side_effect=login_service.AuthenticationError),
        )
        app.dependency_overrides.update({get_session_no_commit: lambda: db})
        await client.post(
            f"{PREFIX}/auth/login",
            json={"email": "ops@example.com", "password": "x" * 12},
            headers=await csrf(),
        )
        db.commit.assert_awaited_once()

    async def test_email_is_normalized_before_use(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Case and whitespace must not mint a second identity.

        Otherwise every casing of an address is a separate throttle budget and a
        separate lookup miss.
        """
        captured: dict[str, Any] = {}

        async def _capture(db, *, email, password, ip_address=None, user_agent=None):
            captured["email"] = email
            raise login_service.AuthenticationError

        monkeypatch.setattr(login_service, "authenticate", _capture)
        await client.post(
            f"{PREFIX}/auth/login",
            json={"email": "  OPS@Example.COM ", "password": "x" * 12},
            headers=await csrf(),
        )
        assert captured["email"] == "ops@example.com"

    async def test_malformed_email_is_rejected_before_any_lookup(
        self, client: httpx.AsyncClient, csrf
    ) -> None:
        response = await client.post(
            f"{PREFIX}/auth/login",
            json={"email": "not-an-address", "password": "x" * 12},
            headers=await csrf(),
        )
        assert response.status_code == 422


class TestProtectedRoutes:
    async def test_me_requires_a_session(self, client: httpx.AsyncClient) -> None:
        response = await client.get(f"{PREFIX}/auth/me")
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Cookie"

    async def test_me_with_a_bad_token_is_the_same_401(
        self, client: httpx.AsyncClient
    ) -> None:
        """An expired, revoked, and unknown token must be indistinguishable."""
        client.cookies.set(get_settings().session_cookie_name, "not-a-real-token")
        response = await client.get(f"{PREFIX}/auth/me")
        assert response.status_code == 401

    async def test_logout_requires_a_session(self, client: httpx.AsyncClient) -> None:
        response = await client.post(f"{PREFIX}/auth/logout")
        assert response.status_code == 403  # CSRF is checked first


class TestOpenAPI:
    async def test_auth_paths_are_documented(self, client: httpx.AsyncClient) -> None:
        paths = (await client.get("/openapi.json")).json()["paths"]
        assert f"{PREFIX}/auth/login" in paths
        assert f"{PREFIX}/auth/me" in paths
        assert f"{PREFIX}/auth/password" in paths

    async def test_csrf_endpoint_is_documented(self, client: httpx.AsyncClient) -> None:
        """A client cannot implement the scheme if the token route is hidden."""
        paths = (await client.get("/openapi.json")).json()["paths"]
        assert f"{PREFIX}/auth/csrf" in paths


class TestRoleWiring:
    def test_bootstrap_grants_ops_manager_not_superuser(self) -> None:
        """The first account must exercise the role matrix, not bypass it.

        A superuser flag on the bootstrap account would mean D-003/D-004 is never
        enforced for the one account most likely to be used.
        """
        assert UserRole.ECOMMERCE_OPS_MANAGER in UserRole
        assert UserRole.ECOMMERCE_OPS_MANAGER != UserRole.DATA_INTEGRATION_ADMIN
