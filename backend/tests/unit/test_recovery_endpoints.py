"""Password reset and invitation endpoint tests (D-017).

The database is mocked and the HTTP layer is real, matching the D-014 endpoint
suite. What is under test is the wire contract: identical responses regardless of
whether an address exists, the status codes, the capability gate on invitation
management, and the fact that no token ever appears in a response body.

The services are stubbed. Their behaviour has its own tests, and a real Argon2
hash per request would add minutes to the suite.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
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
from app.services.auth import invitations as invite_service
from app.services.auth import password_reset as reset_service

PREFIX = "/api/v1"


def _user(**overrides: Any) -> User:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "email": "ops@example.com",
        "hashed_password": "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA",
        "full_name": "Ops Person",
        "is_superuser": False,
        "is_active": True,
    }
    defaults.update(overrides)
    return User(**defaults)


def _fake_session() -> MagicMock:
    session = MagicMock()
    empty = MagicMock()
    empty.scalar_one_or_none.return_value = None
    empty.scalars.return_value = []
    empty.scalar.return_value = None
    session.execute = AsyncMock(return_value=empty)
    session.scalar = AsyncMock(return_value=None)
    session.commit = AsyncMock(return_value=None)
    session.rollback = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.flush = AsyncMock(return_value=None)
    return session


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Real HTTP, mocked database.

    `dependency_overrides` is app-wide state, so it is snapshotted and restored.
    Leaving this module's overrides behind made the D-014 endpoint tests fail
    depending on which file pytest happened to collect first — a failure with no
    relation to the code under test, which is the worst kind.
    """
    previous = dict(app.dependency_overrides)
    db = _fake_session()
    app.dependency_overrides.update({get_session: lambda: db})
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://test", follow_redirects=False
    ) as instance:
        yield instance
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


@pytest.fixture
def csrf(client: httpx.AsyncClient):
    async def _token() -> dict[str, str]:
        await client.get(f"{PREFIX}/auth/csrf")
        token = client.cookies.get(get_settings().csrf_cookie_name)
        assert token is not None, "no CSRF cookie was issued"
        return {get_settings().csrf_header_name: token}

    return _token


def _invitation_record(**overrides) -> MagicMock:
    from datetime import UTC, datetime, timedelta

    defaults = {
        "id": uuid.uuid4(),
        "email": "new@example.com",
        "role": UserRole.DATA_STEWARD,
        "invited_by_user_id": uuid.uuid4(),
        "expires_at": datetime.now(UTC) + timedelta(days=1),
        "accepted_at": None,
        "revoked_at": None,
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    record = MagicMock()
    for key, value in defaults.items():
        setattr(record, key, value)
    return record


class TestPasswordResetRequest:
    async def test_requires_a_csrf_token(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            f"{PREFIX}/auth/password-reset", json={"email": "a@b.com"}
        )
        assert response.status_code == 403

    async def test_returns_202_for_a_known_address(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        request_reset = AsyncMock(return_value=True)
        monkeypatch.setattr(reset_service, "request_reset", request_reset)
        response = await client.post(
            f"{PREFIX}/auth/password-reset",
            json={"email": "ops@example.com"},
            headers=await csrf(),
        )
        assert response.status_code == 202

    async def test_returns_202_for_an_unknown_address(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point, asserted at the wire.

        If this and the known-address response ever differ in status, body, or
        wording, the endpoint becomes an account-existence oracle.
        """
        monkeypatch.setattr(
            reset_service, "request_reset", AsyncMock(return_value=False)
        )
        unknown = await client.post(
            f"{PREFIX}/auth/password-reset",
            json={"email": "nobody@example.com"},
            headers=await csrf(),
        )
        monkeypatch.setattr(
            reset_service, "request_reset", AsyncMock(return_value=True)
        )
        known = await client.post(
            f"{PREFIX}/auth/password-reset",
            json={"email": "ops@example.com"},
            headers=await csrf(),
        )
        assert unknown.status_code == known.status_code == 202
        assert unknown.json() == known.json()

    async def test_body_says_queued_rather_than_sent(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            reset_service, "request_reset", AsyncMock(return_value=True)
        )
        response = await client.post(
            f"{PREFIX}/auth/password-reset",
            json={"email": "ops@example.com"},
            headers=await csrf(),
        )
        body = response.json()
        # The only transport is an outbox row, so the response must not claim a
        # message went anywhere.
        assert body["accepted"] is True
        assert "queued" in body["message"]
        assert "sent" not in body["message"].lower()

    async def test_address_is_normalized_before_the_service_sees_it(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        request_reset = AsyncMock(return_value=True)
        monkeypatch.setattr(reset_service, "request_reset", request_reset)
        await client.post(
            f"{PREFIX}/auth/password-reset",
            json={"email": "  OPS@Example.COM "},
            headers=await csrf(),
        )
        assert request_reset.await_args.kwargs["email"] == "ops@example.com"

    async def test_a_malformed_address_is_rejected(
        self, client: httpx.AsyncClient, csrf
    ) -> None:
        response = await client.post(
            f"{PREFIX}/auth/password-reset",
            json={"email": "not-an-address"},
            headers=await csrf(),
        )
        assert response.status_code == 422


class TestPasswordResetConfirm:
    async def test_requires_a_csrf_token(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            f"{PREFIX}/auth/password-reset/confirm",
            json={"token": "t" * 43, "new_password": "a-long-enough-password"},
        )
        assert response.status_code == 403

    async def test_reports_how_many_sessions_were_revoked(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            reset_service,
            "confirm_reset",
            AsyncMock(
                return_value=reset_service.ResetCompleted(
                    user=_user(), sessions_revoked=4
                )
            ),
        )
        response = await client.post(
            f"{PREFIX}/auth/password-reset/confirm",
            json={"token": "t" * 43, "new_password": "a-long-enough-password"},
            headers=await csrf(),
        )
        assert response.status_code == 200
        assert response.json()["sessions_revoked"] == 4

    async def test_no_session_cookie_is_issued(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The reset came from an inbox; authenticating the presenter would be
        authenticating whoever read the mail."""
        monkeypatch.setattr(
            reset_service,
            "confirm_reset",
            AsyncMock(
                return_value=reset_service.ResetCompleted(
                    user=_user(), sessions_revoked=1
                )
            ),
        )
        await client.get(f"{PREFIX}/auth/csrf")
        client.cookies.clear()
        response = await client.post(
            f"{PREFIX}/auth/password-reset/confirm",
            json={"token": "t" * 43, "new_password": "a-long-enough-password"},
            headers=await csrf(),
        )
        assert get_settings().session_cookie_name not in client.cookies
        assert response.status_code == 200

    async def test_bad_token_is_400_with_one_message(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            reset_service,
            "confirm_reset",
            AsyncMock(side_effect=reset_service.InvalidResetTokenError),
        )
        response = await client.post(
            f"{PREFIX}/auth/password-reset/confirm",
            json={"token": "t" * 43, "new_password": "a-long-enough-password"},
            headers=await csrf(),
        )
        assert response.status_code == 400
        assert "invalid or has expired" in response.json()["detail"]

    async def test_weak_password_is_422(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.security.passwords import PasswordPolicyError

        monkeypatch.setattr(
            reset_service,
            "confirm_reset",
            AsyncMock(side_effect=PasswordPolicyError("password must be at least 12 characters")),
        )
        response = await client.post(
            f"{PREFIX}/auth/password-reset/confirm",
            json={"token": "t" * 43, "new_password": "short"},
            headers=await csrf(),
        )
        assert response.status_code == 422


def _override_tenant(scope_role: str, workspace_id: uuid.UUID) -> dict[Any, Any]:
    user = _user()
    session = MagicMock()
    session.id = uuid.uuid4()
    session.user_id = user.id
    context = AuthContext(user=user, session=session, memberships=())
    scope = TenantScope(
        context=context,
        tenant=TenantContext(workspace_id=workspace_id, user_id=user.id),
        role=scope_role,
    )
    return {
        get_auth_context: lambda: context,
        get_current_tenant: lambda: scope,
    }


class TestInvitationManagement:
    async def test_requires_authentication(
        self, client: httpx.AsyncClient, csrf
    ) -> None:
        """No override: an unauthenticated caller gets nothing."""
        response = await client.post(
            f"{PREFIX}/invitations",
            json={"email": "new@example.com", "role": "data_steward"},
            headers=await csrf(),
        )
        assert response.status_code == 401

    async def test_non_admin_roles_are_refused(
        self, client: httpx.AsyncClient, csrf
    ) -> None:
        """Including the roles that can write and approve.

        This endpoint creates a membership with an arbitrary role, so anything
        short of `admin` would make escalation a property of the role matrix.
        """
        workspace_id = uuid.uuid4()
        app.dependency_overrides.update(
            _override_tenant(str(UserRole.DATA_STEWARD), workspace_id)
        )
        response = await client.post(
            f"{PREFIX}/invitations",
            json={"email": "new@example.com", "role": "data_steward"},
            headers={**await csrf(), "X-Workspace-Id": str(workspace_id)},
        )
        assert response.status_code == 403

    async def test_admin_can_issue(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace_id = uuid.uuid4()
        app.dependency_overrides.update(
            _override_tenant(str(UserRole.ECOMMERCE_OPS_MANAGER), workspace_id)
        )
        record = _invitation_record()
        monkeypatch.setattr(
            invite_service,
            "invite",
            AsyncMock(
                return_value=invite_service.IssuedInvitation(
                    record=record, token="secret-token", queued_message_id=uuid.uuid4()
                )
            ),
        )
        response = await client.post(
            f"{PREFIX}/invitations",
            json={"email": "New@Example.com", "role": "data_steward"},
            headers={**await csrf(), "X-Workspace-Id": str(workspace_id)},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["invitation"]["role"] == "data_steward"
        assert body["resent"] is False
        # The token is not in the response. An admin who can read a pending
        # invitation's link can accept it as anyone.
        assert "secret-token" not in response.text
        assert "token" not in body["invitation"]

    async def test_unknown_role_is_422(
        self, client: httpx.AsyncClient, csrf
    ) -> None:
        workspace_id = uuid.uuid4()
        app.dependency_overrides.update(
            _override_tenant(str(UserRole.ECOMMERCE_OPS_MANAGER), workspace_id)
        )
        response = await client.post(
            f"{PREFIX}/invitations",
            json={"email": "new@example.com", "role": "superadmin"},
            headers={**await csrf(), "X-Workspace-Id": str(workspace_id)},
        )
        assert response.status_code == 422

    async def test_duplicate_pending_invite_points_at_resend(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A double-click should not be an error the operator has to reason about."""
        workspace_id = uuid.uuid4()
        app.dependency_overrides.update(
            _override_tenant(str(UserRole.ECOMMERCE_OPS_MANAGER), workspace_id)
        )
        monkeypatch.setattr(
            invite_service,
            "invite",
            AsyncMock(side_effect=invite_service.InvitationAlreadySentError),
        )
        response = await client.post(
            f"{PREFIX}/invitations",
            json={"email": "new@example.com", "role": "data_steward"},
            headers={**await csrf(), "X-Workspace-Id": str(workspace_id)},
        )
        assert response.status_code == 409
        assert "resend" in response.json()["detail"]

    async def test_revoke_of_an_unknown_invitation_is_404(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace_id = uuid.uuid4()
        app.dependency_overrides.update(
            _override_tenant(str(UserRole.ECOMMERCE_OPS_MANAGER), workspace_id)
        )
        monkeypatch.setattr(invite_service, "revoke", AsyncMock(return_value=False))
        response = await client.delete(
            f"{PREFIX}/invitations/{uuid.uuid4()}",
            headers={**await csrf(), "X-Workspace-Id": str(workspace_id)},
        )
        assert response.status_code == 404

    async def test_list_never_exposes_token_material(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace_id = uuid.uuid4()
        app.dependency_overrides.update(
            _override_tenant(str(UserRole.ECOMMERCE_OPS_MANAGER), workspace_id)
        )
        monkeypatch.setattr(
            invite_service,
            "list_invitations",
            AsyncMock(return_value=[_invitation_record()]),
        )
        response = await client.get(
            f"{PREFIX}/invitations",
            headers={"X-Workspace-Id": str(workspace_id)},
        )
        assert response.status_code == 200
        assert response.json()["invitations"][0]["status"] == "pending"
        assert "token" not in response.text


class TestInvitationAcceptance:
    async def test_requires_a_csrf_token(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            f"{PREFIX}/auth/invitations/accept", json={"token": "t" * 43}
        )
        assert response.status_code == 403

    async def test_works_without_a_session(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The invited person has no account yet, which is the entire use case."""
        workspace_id = uuid.uuid4()
        user = _user(email="new@example.com")
        membership = MagicMock(workspace_id=workspace_id, role=UserRole.DATA_STEWARD)
        monkeypatch.setattr(
            invite_service,
            "accept",
            AsyncMock(
                return_value=invite_service.AcceptedInvitation(
                    user=user, membership=membership, created_user=True
                )
            ),
        )
        response = await client.post(
            f"{PREFIX}/auth/invitations/accept",
            json={
                "token": "t" * 43,
                "password": "a-long-enough-password",
                "full_name": "Nia New",
            },
            headers=await csrf(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["workspace_id"] == str(workspace_id)
        assert body["role"] == "data_steward"
        assert body["account_created"] is True
        # No session: the new user signs in like anyone else.
        assert get_settings().session_cookie_name not in client.cookies

    async def test_dead_token_is_400(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            invite_service,
            "accept",
            AsyncMock(side_effect=invite_service.InvitationNotFoundError),
        )
        response = await client.post(
            f"{PREFIX}/auth/invitations/accept",
            json={"token": "t" * 43, "password": "a-long-enough-password"},
            headers=await csrf(),
        )
        assert response.status_code == 400

    async def test_already_a_member_is_409(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            invite_service,
            "accept",
            AsyncMock(
                side_effect=invite_service.InvitationRecipientInvalidError(
                    "That address is already a member of this workspace"
                )
            ),
        )
        response = await client.post(
            f"{PREFIX}/auth/invitations/accept",
            json={"token": "t" * 43, "password": "a-long-enough-password"},
            headers=await csrf(),
        )
        assert response.status_code == 409

    async def test_weak_password_is_422(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.security.passwords import PasswordPolicyError

        monkeypatch.setattr(
            invite_service,
            "accept",
            AsyncMock(side_effect=PasswordPolicyError("too short")),
        )
        response = await client.post(
            f"{PREFIX}/auth/invitations/accept",
            json={"token": "t" * 43, "password": "short"},
            headers=await csrf(),
        )
        assert response.status_code == 422
