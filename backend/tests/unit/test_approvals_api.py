"""Approval-center endpoints (Phase 18, §20, §38).

The wire contract, following the compliance router's pattern: real HTTP over
in-process ASGI, database mocked, session injected per role.

Two claims are pinned here that the workflow tests cannot reach. The first is
the capability gate — the data integration admin holds credential custody and
must never approve (D-003, D-004), so a 403 for that role proves the matrix is
what the route is standing behind. The second is that every route needs a
session: an unauthenticated caller gets 401 without any database access, which
is what keeps the gate working when Postgres is unreachable.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport

from app.api.deps import AuthContext, TenantScope, get_auth_context, get_current_tenant
from app.core.config import get_settings
from app.db.base import TenantContext
from app.db.session import get_session
from app.main import app
from app.models.tenancy import User, UserRole

PREFIX = "/api/v1"
QUEUE = f"{PREFIX}/approvals"
WORKSPACE = uuid.uuid4()
KNOWN_PROPOSAL = uuid.UUID("11111111-1111-1111-1111-111111111111")
UNKNOWN_PROPOSAL = uuid.UUID("22222222-2222-2222-2222-222222222222")

APPROVER_ROLES = (
    UserRole.ECOMMERCE_OPS_MANAGER.value,
    UserRole.DATA_STEWARD.value,
)
# The one that matters most: it holds credential custody, and D-004 says that
# custody must never come with approval authority.
NON_APPROVER_ROLES = (
    UserRole.DATA_INTEGRATION_ADMIN.value,
    UserRole.MERCHANDISER.value,
    UserRole.SEO_SPECIALIST.value,
)


def _user() -> User:
    return User(
        id=uuid.uuid4(),
        email="approver@example.com",
        hashed_password="$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA",
        full_name="Approver Person",
        is_active=True,
    )


def _session() -> MagicMock:
    """An empty database: no proposals, no requests, nothing to decide."""
    session = MagicMock()
    empty = MagicMock()
    empty.scalar_one_or_none.return_value = None
    empty.all.return_value = []
    empty.scalars.return_value = empty
    session.execute = AsyncMock(return_value=empty)
    session.commit = AsyncMock(return_value=None)
    session.rollback = AsyncMock(return_value=None)
    return session


def _override(role: str) -> dict[Any, Any]:
    user = _user()
    session = MagicMock()
    session.id = uuid.uuid4()
    session.user_id = user.id
    context = AuthContext(user=user, session=session, memberships=())
    scope = TenantScope(
        context=context,
        tenant=TenantContext(workspace_id=WORKSPACE, user_id=user.id),
        role=role,
    )
    return {
        get_auth_context: lambda: context,
        get_current_tenant: lambda: scope,
        get_session: lambda: _session(),
    }


@pytest.fixture
def client() -> httpx.AsyncClient:
    """`https://` so the Secure CSRF cookie is actually sent back."""
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
        follow_redirects=False,
    )


@pytest.fixture
def csrf(client: httpx.AsyncClient):
    async def _token() -> dict[str, str]:
        await client.get(f"{PREFIX}/auth/csrf")
        token = client.cookies.get(get_settings().csrf_cookie_name)
        assert token is not None, "no CSRF cookie was issued"
        return {get_settings().csrf_header_name: token}

    return _token


class TestApprovalsApi:
    async def test_routes_are_in_the_spec(self) -> None:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            paths = (await http.get("/openapi.json")).json()["paths"]

        assert QUEUE in paths
        assert f"{QUEUE}/{{proposal_id}}" in paths
        assert f"{QUEUE}/{{proposal_id}}/approve" in paths
        assert f"{QUEUE}/{{proposal_id}}/reject" in paths
        assert f"{QUEUE}/{{proposal_id}}/modify" in paths
        assert f"{QUEUE}/{{proposal_id}}/re-analysis" in paths
        assert f"{QUEUE}/approve-low-risk" in paths
        # The bulk shape is POST to the collection itself, not a second
        # collection route: §20's "selected" is a set of ids in one body.
        assert "post" in paths[QUEUE]
        assert "get" in paths[QUEUE]

    async def test_every_route_requires_a_session(
        self, client: httpx.AsyncClient, csrf
    ) -> None:
        """No override: an unauthenticated caller gets 401, never 500."""
        assert (await client.get(QUEUE)).status_code == 401
        detail = await client.get(f"{QUEUE}/{KNOWN_PROPOSAL}")
        assert detail.status_code == 401

        headers = await csrf()
        for path, body in (
            (f"{QUEUE}/{KNOWN_PROPOSAL}/approve", {}),
            (f"{QUEUE}/{KNOWN_PROPOSAL}/reject", {"notes": "no"}),
            (f"{QUEUE}/{KNOWN_PROPOSAL}/re-analysis", {"notes": "look again"}),
            (QUEUE, {"action": "approve", "proposal_ids": [str(KNOWN_PROPOSAL)]}),
            (f"{QUEUE}/approve-low-risk", None),
        ):
            response = await client.post(
                path, json=body, headers=headers
            )
            assert response.status_code == 401, path

    async def test_roles_that_cannot_approve_are_refused(
        self, client: httpx.AsyncClient, csrf
    ) -> None:
        """The gate is the capability matrix, and D-004's role is in it.

        Reading the queue and writing a decision are both refused, because the
        approval center is only useful to someone who may decide.
        """
        headers = await csrf()
        for role in NON_APPROVER_ROLES:
            app.dependency_overrides.update(_override(role))
            try:
                response = await client.get(
                    QUEUE, headers={"X-Workspace-Id": str(WORKSPACE)}
                )
                assert response.status_code == 403, role

                response = await client.post(
                    f"{QUEUE}/{KNOWN_PROPOSAL}/approve",
                    json={},
                    headers={**headers, "X-Workspace-Id": str(WORKSPACE)},
                )
                assert response.status_code == 403, role
            finally:
                app.dependency_overrides.clear()

    @pytest.mark.parametrize("role", APPROVER_ROLES)
    async def test_roles_that_can_approve_can_read_the_queue(
        self, client: httpx.AsyncClient, role: str
    ) -> None:
        app.dependency_overrides.update(_override(role))
        try:
            response = await client.get(
                QUEUE, headers={"X-Workspace-Id": str(WORKSPACE)}
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0

    async def test_an_unknown_proposal_is_a_404_not_a_500(
        self, client: httpx.AsyncClient, csrf
    ) -> None:
        """The database is mocked to return nothing; the route still answers.

        Which is the property that matters: a proposal that is not there is a
        404 the caller can act on, not an unhandled `None`.
        """
        app.dependency_overrides.update(_override(UserRole.DATA_STEWARD.value))
        try:
            detail = await client.get(
                f"{QUEUE}/{UNKNOWN_PROPOSAL}",
                headers={"X-Workspace-Id": str(WORKSPACE)},
            )
            approve = await client.post(
                f"{QUEUE}/{UNKNOWN_PROPOSAL}/approve",
                json={},
                headers={
                    **await csrf(),
                    "X-Workspace-Id": str(WORKSPACE),
                },
            )
        finally:
            app.dependency_overrides.clear()

        assert detail.status_code == 404
        assert approve.status_code == 404

    async def test_validation_still_fires_when_authentication_succeeds(
        self, client: httpx.AsyncClient, csrf
    ) -> None:
        """A rejection with no reason never reaches the workflow (§39)."""
        app.dependency_overrides.update(_override(UserRole.DATA_STEWARD.value))
        try:
            response = await client.post(
                f"{QUEUE}/{KNOWN_PROPOSAL}/reject",
                json={"notes": ""},
                headers={
                    **await csrf(),
                    "X-Workspace-Id": str(WORKSPACE),
                },
            )
            bulk = await client.post(
                QUEUE,
                json={"action": "reject", "proposal_ids": [str(KNOWN_PROPOSAL)]},
                headers={
                    **await csrf(),
                    "X-Workspace-Id": str(WORKSPACE),
                },
            )
            missing_value = await client.post(
                f"{QUEUE}/{KNOWN_PROPOSAL}/modify",
                json={"notes": "use the darker navy"},
                headers={
                    **await csrf(),
                    "X-Workspace-Id": str(WORKSPACE),
                },
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422
        assert bulk.status_code == 422
        assert missing_value.status_code == 422
