"""Sign-in against a real database (Phase 24, §40).

Authentication is the one path where a mock is least like the thing: D-014's
guarantee is that only a SHA-256 digest of the cookie token is stored, which is
a claim about a column, and a claim about a column is not true until a database
has written and read it back.

So no overrides beyond the session itself. The password is hashed with Argon2id,
the session row is written by the real service, the cookie is issued by the real
route, and the assertions read the table with SQL rather than trusting the
response that reported the write.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.security.passwords import hash_password
from app.db.session import get_session, get_session_no_commit
from app.main import app
from app.models.tenancy import UserRole

PREFIX = "/api/v1"
PASSWORD = "correct horse battery staple"


async def _truncate(engine: Any) -> None:
    """Empty the auth tables between tests.

    Truncating rather than rolling back is the suite's convention: a rolled-back
    fixture would hide the commit-time behaviour these tests assert.
    """
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE workspace_memberships, auth_sessions, users, "
                "workspaces, organizations CASCADE"
            )
        )


async def _seed(engine: Any, *, role: UserRole = UserRole.ECOMMERCE_OPS_MANAGER) -> dict[str, Any]:
    """An organization, a workspace, one member with a real Argon2id hash."""

    # Written with SQL rather than through the ORM because `users`,
    # `organizations` and `workspaces` are global tables (D-011) — which is
    # exactly how login reads them — and because the fixture is about what the
    # database accepts, not about what the ORM can express.
    organization_id, workspace_id, user_id = uuid4(), uuid4(), uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug, is_active) "
                "VALUES (:id, 'Acme', :slug, true)"
            ),
            {"id": organization_id, "slug": f"acme-{uuid4().hex[:8]}"},
        )
        await connection.execute(
            text(
                "INSERT INTO workspaces (id, organization_id, name, slug) "
                "VALUES (:id, :org, 'Catalog', :slug)"
            ),
            {"id": workspace_id, "org": organization_id, "slug": f"ws-{uuid4().hex[:8]}"},
        )
        await connection.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, full_name, "
                "is_active, is_superuser) VALUES (:id, 'ops@example.com', :hash, "
                "'Ops Person', true, false)"
            ),
            {"id": user_id, "hash": hash_password(PASSWORD)},
        )
        await connection.execute(
            text(
                "INSERT INTO workspace_memberships (id, workspace_id, user_id, role, "
                "created_at, updated_at) VALUES (:id, :ws, :user, :role, now(), now())"
            ),
            {
                "id": uuid4(),
                "ws": workspace_id,
                "user": user_id,
                "role": role.value,
            },
        )

    return {"workspace_id": workspace_id, "user_id": user_id, "email": "ops@example.com"}


@pytest_asyncio.fixture
async def api(engine: Any, app_session_factory: async_sessionmaker[AsyncSession]):
    """An HTTP client whose sessions run as the application role.

    Only `get_session` is overridden, and only to point at the migrated test
    database as the non-superuser application role — the role that actually owns
    the tables in production. Auth itself is not stubbed: the real Argon2id
    verification, the real session row and the real cookie all run.
    """
    await _truncate(engine)

    async def _session():
        async with app_session_factory() as session:
            try:
                yield session
            finally:
                await session.rollback()

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_session_no_commit] = _session
    # `https://` so the Secure session cookie is returned to the client, the
    # same reason the unit suite does this.
    client = httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test", follow_redirects=False
    )
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
        await client.aclose()


@pytest.fixture
async def csrf_token(api: httpx.AsyncClient) -> dict[str, str]:
    settings = get_settings()
    await api.get(f"{PREFIX}/auth/csrf")
    token = api.cookies.get(settings.csrf_cookie_name)
    assert token is not None, "the CSRF endpoint issued no cookie"

    return {settings.csrf_header_name: token}


@pytest.mark.integration
class TestSignInAgainstARealDatabase:
    async def test_a_correct_password_issues_a_session_row_and_a_cookie(
        self, api: httpx.AsyncClient, csrf_token: dict[str, str], engine: Any
    ) -> None:
        seeded = await _seed(engine)

        response = await api.post(
            f"{PREFIX}/auth/login",
            json={"email": seeded["email"], "password": PASSWORD},
            headers=csrf_token,
        )

        assert response.status_code == 200, response.text
        settings = get_settings()
        cookie = api.cookies.get(settings.session_cookie_name)
        assert cookie, "no session cookie was issued"
        # D-014: the cookie is not the stored token.
        assert cookie not in response.text

        async with engine.connect() as connection:
            stored = (
                await connection.execute(
                    text(
                        "SELECT token_hash, user_id, revoked_at FROM auth_sessions "
                        "WHERE user_id = :user"
                    ),
                    {"user": seeded["user_id"]},
                )
            ).all()

        assert len(stored) == 1, "expected exactly one session row"
        assert stored[0].token_hash != cookie
        assert stored[0].revoked_at is None

    async def test_the_stored_token_is_a_digest_of_the_cookie(
        self, api: httpx.AsyncClient, csrf_token: dict[str, str], engine: Any
    ) -> None:
        """The stored column is a SHA-256 of what the browser holds."""

        import hashlib

        seeded = await _seed(engine)
        await api.post(
            f"{PREFIX}/auth/login",
            json={"email": seeded["email"], "password": PASSWORD},
            headers=csrf_token,
        )
        settings = get_settings()
        cookie = api.cookies.get(settings.session_cookie_name) or ""

        async with engine.connect() as connection:
            stored = (
                await connection.execute(text("SELECT token_hash FROM auth_sessions"))
            ).scalar_one()

        assert stored == hashlib.sha256(cookie.encode()).hexdigest()

    async def test_a_wrong_password_writes_no_session(
        self, api: httpx.AsyncClient, csrf_token: dict[str, str], engine: Any
    ) -> None:
        seeded = await _seed(engine)

        response = await api.post(
            f"{PREFIX}/auth/login",
            json={"email": seeded["email"], "password": "not the password"},
            headers=csrf_token,
        )

        assert response.status_code == 401
        async with engine.connect() as connection:
            count = (
                await connection.execute(text("SELECT count(*) FROM auth_sessions"))
            ).scalar_one()

        assert count == 0

    async def test_an_unknown_email_is_indistinguishable_from_a_wrong_password(
        self, api: httpx.AsyncClient, csrf_token: dict[str, str], engine: Any
    ) -> None:
        """A login response must not confirm which addresses exist."""

        await _seed(engine)

        unknown = await api.post(
            f"{PREFIX}/auth/login",
            json={"email": "nobody@example.com", "password": PASSWORD},
            headers=csrf_token,
        )
        wrong = await api.post(
            f"{PREFIX}/auth/login",
            json={"email": "ops@example.com", "password": "not the password"},
            headers=csrf_token,
        )

        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["detail"] == wrong.json()["detail"]

    async def test_the_session_cookie_authenticates_a_real_request(
        self, api: httpx.AsyncClient, csrf_token: dict[str, str], engine: Any
    ) -> None:
        seeded = await _seed(engine)
        await api.post(
            f"{PREFIX}/auth/login",
            json={"email": seeded["email"], "password": PASSWORD},
            headers=csrf_token,
        )

        me = await api.get(f"{PREFIX}/auth/me")

        assert me.status_code == 200, me.text
        assert me.json()["user"]["email"] == seeded["email"]
        workspaces = me.json()["workspaces"]

        assert [w["id"] for w in workspaces] == [str(seeded["workspace_id"])]
        # The role travels with the membership, because D-003's roles are
        # per-workspace rather than global.
        assert workspaces[0]["role"] == UserRole.ECOMMERCE_OPS_MANAGER.value

    async def test_logout_revokes_the_row_and_the_cookie_stops_working(
        self, api: httpx.AsyncClient, csrf_token: dict[str, str], engine: Any
    ) -> None:
        """D-014's revocation is a database fact, not a client-side hint."""

        seeded = await _seed(engine)
        await api.post(
            f"{PREFIX}/auth/login",
            json={"email": seeded["email"], "password": PASSWORD},
            headers=csrf_token,
        )
        assert (await api.get(f"{PREFIX}/auth/me")).status_code == 200

        logout = await api.post(f"{PREFIX}/auth/logout", headers=csrf_token)
        assert logout.status_code == 200

        async with engine.connect() as connection:
            revoked = (
                await connection.execute(text("SELECT revoked_at FROM auth_sessions"))
            ).scalar_one()

        assert revoked is not None, "logout did not mark the session revoked"
        assert (await api.get(f"{PREFIX}/auth/me")).status_code == 401

    async def test_a_revoked_cookie_cannot_be_replayed(
        self, api: httpx.AsyncClient, csrf_token: dict[str, str], engine: Any
    ) -> None:
        """Revocation must survive the client keeping the cookie."""

        seeded = await _seed(engine)
        await api.post(
            f"{PREFIX}/auth/login",
            json={"email": seeded["email"], "password": PASSWORD},
            headers=csrf_token,
        )
        settings = get_settings()
        stolen = api.cookies.get(settings.session_cookie_name)
        await api.post(f"{PREFIX}/auth/logout", headers=csrf_token)

        replay = httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="https://test"
        )
        replay.cookies.set(settings.session_cookie_name, stolen or "")
        try:
            assert (await replay.get(f"{PREFIX}/auth/me")).status_code == 401
        finally:
            await replay.aclose()

    async def test_a_user_with_two_sessions_still_authenticates(
        self, api: httpx.AsyncClient, csrf_token: dict[str, str], engine: Any
    ) -> None:
        """Signing in twice must not break the account.

        `get_auth_context` joined `auth_sessions` onto a lookup already
        constrained to the session's own user, so a second session matched the
        row twice and `scalar_one_or_none` raised `MultipleResultsFound` —
        every authenticated request answered 500. Phase 25 found this by
        signing in twice in one browser and watching the console break.
        """
        seeded = await _seed(engine)
        for _ in range(2):
            response = await api.post(
                f"{PREFIX}/auth/login",
                json={"email": seeded["email"], "password": PASSWORD},
                headers=csrf_token,
            )
            assert response.status_code == 200, response.text

        async with engine.connect() as connection:
            sessions = (
                await connection.execute(text("SELECT count(*) FROM auth_sessions"))
            ).scalar_one()
        assert sessions == 2, "both sign-ins should have created a session row"

        me = await api.get(f"{PREFIX}/auth/me")

        assert me.status_code == 200, me.text
        assert me.json()["user"]["email"] == seeded["email"]
