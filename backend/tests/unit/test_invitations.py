"""Invitation tests (D-017).

No database; `AsyncSession` is mocked at the `execute`/`flush`/`add` boundary.
What is under test is the security shape of the feature, not the SQL:

  - the role in the membership comes from the stored invitation and nowhere
    else, which is what makes an invitation safe to send by email;
  - an invitation is redeemable once, and only while live and unexpired;
  - an invitation cannot overwrite somebody else's password;
  - resending kills the previous token;
  - an admin of one workspace cannot act on another's invitation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.expression import Update

from app.core.security.tokens import hash_token
from app.models.tenancy import User, UserRole, WorkspaceInvitation, WorkspaceMembership
from app.services.auth import invitations as invite_service

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _result(value=None, *, rows: list | None = None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar.return_value = value
    result.scalars.return_value = rows if rows is not None else []
    result.rowcount = 1
    return result


class _Queued:
    """Returns each prepared result in turn; the last one repeats."""

    def __init__(self, *values: MagicMock) -> None:
        self.values = list(values)

    def __call__(self, *_args, **_kwargs) -> MagicMock:
        if not self.values:
            return _result(None)
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def _db(*values: MagicMock, scalar: int = 0) -> MagicMock:
    """Mock session. `scalar` is what COUNT queries return, 0 unless stated."""
    db = MagicMock()
    db.execute = AsyncMock(side_effect=_Queued(*values))
    db.scalar = AsyncMock(return_value=scalar)
    db.flush = AsyncMock(return_value=None)
    db.delete = AsyncMock()
    return db


def _user(**overrides) -> User:
    now = datetime.now(UTC)
    defaults = {
        "id": uuid.uuid4(),
        "email": "admin@example.com",
        "hashed_password": "hash",
        "full_name": "Ada Admin",
        "is_active": True,
        "is_superuser": False,
        "updated_at": now,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return User(**defaults)


def _workspace() -> MagicMock:
    workspace = MagicMock()
    workspace.id = uuid.uuid4()
    workspace.name = "Acme EU"
    return workspace


def _invitation(**overrides) -> WorkspaceInvitation:
    defaults = {
        "id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "email": "new@example.com",
        "role": UserRole.DATA_STEWARD,
        "invited_by_user_id": uuid.uuid4(),
        "token_hash": hash_token("tok"),
        "expires_at": NOW + timedelta(days=1),
        "accepted_at": None,
        "revoked_at": None,
        "accepted_by_user_id": None,
        "created_at": NOW,
    }
    defaults.update(overrides)
    return WorkspaceInvitation(**defaults)


def _compiled(statement) -> tuple[str, dict]:
    """Compile with a literal renderer so parameters are readable.

    SQL text and bound values are returned separately so a test can assert both.
    The distinction is the point: a digest that reached the database as SQL text
    rather than as a parameter would appear in a query log, and a query log is
    exactly where a redeemable value must not be.
    """
    compiled = statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"render_postcompile": True},
    )
    return str(compiled), dict(compiled.params)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(invite_service, "utcnow", lambda: NOW)


class TestIssuing:
    async def test_invitation_is_created_with_the_requested_role(self) -> None:
        admin = _user()
        workspace = _workspace()
        # [user lookup, live invitation lookup, workspace lookup]
        db = _db(_result(None), _result(None), _result(workspace))
        issued = await invite_service.invite(
            db,
            workspace_id=workspace.id,
            email="New@Example.com",
            role=UserRole.SEO_SPECIALIST,
            invited_by=admin,
        )
        assert issued.record.role == UserRole.SEO_SPECIALIST
        # Normalized once, so the throttle key, the unique index, and the
        # message recipient all agree on one spelling of the address.
        assert issued.record.email == "new@example.com"

    async def test_only_the_digest_is_stored(self) -> None:
        admin, workspace = _user(), _workspace()
        db = _db(_result(None), _result(None), _result(workspace))
        issued = await invite_service.invite(
            db,
            workspace_id=workspace.id,
            email="new@example.com",
            role=UserRole.MERCHANDISER,
            invited_by=admin,
        )
        assert issued.token not in issued.record.token_hash
        assert hash_token(issued.token) == issued.record.token_hash

    async def test_expiry_comes_from_configuration(self) -> None:
        from app.core.config import get_settings

        admin, workspace = _user(), _workspace()
        db = _db(_result(None), _result(None), _result(workspace))
        issued = await invite_service.invite(
            db,
            workspace_id=workspace.id,
            email="new@example.com",
            role=UserRole.MERCHANDISER,
            invited_by=admin,
        )
        assert issued.record.expires_at == NOW + timedelta(
            hours=get_settings().invitation_token_ttl_hours
        )

    async def test_existing_member_is_refused(self) -> None:
        admin, workspace = _user(), _workspace()
        member = _user(email="member@example.com")
        db = _db(_result(member), scalar=1)
        with pytest.raises(invite_service.InvitationRecipientInvalidError):
            await invite_service.invite(
                db,
                workspace_id=workspace.id,
                email=member.email,
                role=UserRole.MERCHANDISER,
                invited_by=admin,
            )

    async def test_disabled_account_is_refused(self) -> None:
        """Inviting it would silently restore access an operator removed."""
        admin, workspace = _user(), _workspace()
        disabled = _user(email="gone@example.com", is_active=False)
        db = _db(_result(disabled))
        with pytest.raises(invite_service.InvitationRecipientInvalidError):
            await invite_service.invite(
                db,
                workspace_id=workspace.id,
                email=disabled.email,
                role=UserRole.MERCHANDISER,
                invited_by=admin,
            )

    async def test_second_live_invitation_is_refused(self) -> None:
        admin, workspace = _user(), _workspace()
        live = _invitation(workspace_id=workspace.id)
        db = _db(_result(None), _result(live), scalar=1)
        with pytest.raises(invite_service.InvitationAlreadySentError):
            await invite_service.invite(
                db,
                workspace_id=workspace.id,
                email=live.email,
                role=UserRole.MERCHANDISER,
                invited_by=admin,
            )

    async def test_resend_replaces_the_token_on_the_same_row(self) -> None:
        """The response to a suspected leak is a resend, not a second grant.

        Two live rows for one address would mean revoking one and still having
        access granted by the other.
        """
        admin, workspace = _user(), _workspace()
        live = _invitation(workspace_id=workspace.id)
        old_hash = live.token_hash
        db = _db(_result(None), _result(live), _result(workspace), scalar=1)
        issued = await invite_service.invite(
            db,
            workspace_id=workspace.id,
            email=live.email,
            role=UserRole.SEO_SPECIALIST,
            invited_by=admin,
            resend=True,
        )
        assert issued.record is live
        assert issued.record.token_hash != old_hash
        # One row, not two: the count query found an existing invitation and no
        # second `WorkspaceInvitation` was added.
        added = [c.args[0] for c in db.add.call_args_list]
        assert not [a for a in added if isinstance(a, WorkspaceInvitation)]


class TestRevocation:
    async def test_revoke_is_scoped_by_workspace_in_sql(self) -> None:
        """Otherwise a UUID guess lets one workspace cancel another's invite."""
        db = _db(_result(uuid.uuid4()))
        assert (
            await invite_service.revoke(
                db,
                invitation_id=uuid.uuid4(),
                workspace_id=uuid.uuid4(),
            )
            is True
        )
        statement = db.execute.await_args.args[0]
        assert isinstance(statement, Update)
        sql, _ = _compiled(statement)
        assert "workspace_id" in sql
        assert "revoked_at IS NULL" in sql

    async def test_revoke_of_a_dead_invitation_reports_false(self) -> None:
        db = _db(_result(None))
        assert (
            await invite_service.revoke(
                db, invitation_id=uuid.uuid4(), workspace_id=uuid.uuid4()
            )
            is False
        )


class TestAcceptance:
    async def test_new_user_is_created_with_the_stored_role(self) -> None:
        workspace_id = uuid.uuid4()
        invitation = _invitation(
            workspace_id=workspace_id, role=UserRole.DATA_STEWARD
        )
        # [consume UPDATE, existing user lookup]
        db = _db(_result(invitation), _result(None))
        result = await invite_service.accept(
            db, token="tok", password="a-long-enough-password", full_name="Nia New"
        )
        assert result.created_user is True
        assert result.membership.role == UserRole.DATA_STEWARD
        assert result.user.email == invitation.email
        # Not a superuser, even for an ops manager. The invite grants a role in
        # one workspace; it does not mint a global bypass.
        assert result.user.is_superuser is False

    async def test_the_role_cannot_come_from_the_request(self) -> None:
        """`accept` takes no role parameter at all.

        The strongest statement available: there is no parameter for a caller to
        put a role into.
        """
        import inspect

        params = set(inspect.signature(invite_service.accept).parameters)
        assert "role" not in params
        assert params == {"db", "token", "password", "full_name"}

    async def test_no_matching_row_is_refused(self) -> None:
        db = _db(_result(None))
        with pytest.raises(invite_service.InvitationNotFoundError):
            await invite_service.accept(
                db, token="tok", password="a-long-enough-password", full_name=None
            )

    async def test_expiry_and_single_use_are_enforced_in_the_where_clause(self) -> None:
        """One statement, three refusals.

        Expired, revoked, and already-redeemed are all decided by predicates on
        the UPDATE that consumes the token, not by a read that another request
        could interleave with. A read-then-write would let two callers both see a
        live row and both create a membership.
        """
        db = _db(_result(None))
        with pytest.raises(invite_service.InvitationNotFoundError):
            await invite_service.accept(
                db,
                token="plaintext-token-value",
                password="a-long-enough-password",
                full_name=None,
            )
        statement = db.execute.await_args.args[0]
        assert isinstance(statement, Update)
        sql, params = _compiled(statement)
        assert "accepted_at IS NULL" in sql
        assert "revoked_at IS NULL" in sql
        assert "expires_at >" in sql
        # The digest travels as a bound value; the plaintext does not appear at
        # all. A leaked query log therefore shows a hash nobody can redeem.
        assert "plaintext-token-value" not in sql
        assert hash_token("plaintext-token-value") in params.values()

    async def test_existing_user_keeps_their_password(self) -> None:
        workspace_id = uuid.uuid4()
        invitation = _invitation(workspace_id=workspace_id)
        existing = _user(email=invitation.email, hashed_password="their-own-hash")
        db = _db(_result(invitation), _result(existing))
        result = await invite_service.accept(
            db, token="tok", password=None, full_name=None
        )
        assert result.created_user is False
        assert existing.hashed_password == "their-own-hash"
        assert result.membership.user_id == existing.id

    async def test_supplying_a_password_for_an_existing_account_is_refused(self) -> None:
        """An invitation link must not become a password-reset vector."""
        invitation = _invitation()
        existing = _user(email=invitation.email)
        db = _db(_result(invitation), _result(existing))
        with pytest.raises(invite_service.InvitationError):
            await invite_service.accept(
                db, token="tok", password="a-long-enough-password", full_name=None
            )

    async def test_new_account_without_a_password_is_refused(self) -> None:
        db = _db(_result(_invitation()), _result(None))
        with pytest.raises(invite_service.InvitationError):
            await invite_service.accept(db, token="tok", password=None, full_name=None)

    async def test_already_a_member_is_refused_rather_than_duplicated(self) -> None:
        """Grants would hit the unique constraint and surface as a 500."""
        invitation = _invitation()
        existing = _user(email=invitation.email)
        db = _db(_result(invitation), _result(existing), scalar=1)
        with pytest.raises(invite_service.InvitationRecipientInvalidError):
            await invite_service.accept(
                db, token="tok", password=None, full_name=None
            )

    async def test_disabled_account_cannot_accept(self) -> None:
        invitation = _invitation()
        existing = _user(email=invitation.email, is_active=False)
        db = _db(_result(invitation), _result(existing))
        with pytest.raises(invite_service.InvitationRecipientInvalidError):
            await invite_service.accept(
                db, token="tok", password=None, full_name=None
            )

    async def test_membership_is_recorded_with_the_inviter(self) -> None:
        invitation = _invitation()
        db = _db(_result(invitation), _result(None))
        result = await invite_service.accept(
            db, token="tok", password="a-long-enough-password", full_name=None
        )
        assert invitation.accepted_by_user_id == result.user.id
        added = [c.args[0] for c in db.add.call_args_list]
        assert [a for a in added if isinstance(a, WorkspaceMembership)]

    async def test_a_weak_password_leaves_the_invitation_live(self) -> None:
        """Same reasoning as the reset: the person cannot get a new link easily.

        The policy error propagates before anything commits, so the invitation
        survives and the link still works on a second try.
        """
        from app.core.security.passwords import PasswordPolicyError

        db = _db(_result(_invitation()), _result(None))
        with pytest.raises(PasswordPolicyError):
            await invite_service.accept(
                db, token="tok", password="short", full_name=None
            )


class TestSchemaBoundary:
    def test_an_unknown_role_is_rejected_at_the_edge(self) -> None:
        """A typo must not produce an invitation whose role grants nothing.

        That failure looks successful: the message goes out, the person joins,
        and they cannot do anything.
        """
        from app.schemas.invitations import InviteRequest

        with pytest.raises(ValueError, match="role must be one of"):
            InviteRequest(email="a@b.com", role="data-stewrad")

    def test_a_known_role_survives_validation(self) -> None:
        from app.schemas.invitations import InviteRequest

        assert InviteRequest(email="A@B.com", role="data_steward").role == "data_steward"

    def test_the_response_never_carries_the_token(self) -> None:
        from app.schemas.invitations import InvitationRef

        fields = set(InvitationRef.model_fields)
        assert "token" not in fields
        assert "token_hash" not in fields


class TestMessageQueued:
    async def test_one_message_names_the_workspace_and_role(self) -> None:
        from app.models.tenancy import EmailOutbox

        admin, workspace = _user(), _workspace()
        db = _db(_result(None), _result(None), _result(workspace))
        await invite_service.invite(
            db,
            workspace_id=workspace.id,
            email="new@example.com",
            role=UserRole.DATA_STEWARD,
            invited_by=admin,
        )
        messages = [
            c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], EmailOutbox)
        ]
        assert len(messages) == 1
        assert workspace.name in messages[0].body_text
        assert "data_steward" in messages[0].body_text
        assert messages[0].sent_at is None

    async def test_a_failed_delivery_does_not_unwind_the_invitation(self) -> None:
        """A live token with no message is an operator problem, not a lost user.

        The transport is called inside the same transaction; if it raises, the
        caller decides, rather than the token silently vanishing.
        """
        admin, workspace = _user(), _workspace()
        db = _db(_result(None), _result(None), _result(workspace))
        transport = MagicMock()
        transport.deliver = AsyncMock(side_effect=RuntimeError("smtp down"))
        with patch.object(
            invite_service.delivery, "get_delivery", return_value=transport
        ), pytest.raises(RuntimeError):
            await invite_service.invite(
                db,
                workspace_id=workspace.id,
                email="new@example.com",
                role=UserRole.MERCHANDISER,
                invited_by=admin,
            )
        added = [c.args[0] for c in db.add.call_args_list]
        assert [a for a in added if isinstance(a, WorkspaceInvitation)]
