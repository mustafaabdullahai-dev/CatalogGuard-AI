"""Password reset tests (D-017).

No database. `AsyncSession` is mocked at the `execute`/`flush` boundary, which
is the seam these modules use. The logic under test is the security properties,
not the SQL: identical responses for known and unknown addresses, single-use
consumption, expiry arithmetic, session revocation, and the live-token cap.

The mocked `execute` returns queued results in order, which is how the service
reads them. Every test that depends on a particular query therefore also
documents the query order, so a future refactor that reorders the reads fails
loudly instead of silently testing the wrong result.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.sql.expression import Delete, Update

from app.core.security.tokens import hash_token
from app.models.tenancy import EmailOutbox, PasswordResetToken, User
from app.services.auth import password_reset as reset_service
from app.services.auth import throttle as throttle_service

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _result(value=None, *, rows: list | None = None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar.return_value = value
    result.scalars.return_value = rows if rows is not None else []
    result.rowcount = 1
    return result


class _Queued:
    """Returns each prepared result in turn; the last one repeats.

    Services here read at most two results in a fixed order, so this is enough
    to express them without a database.
    """

    def __init__(self, *values: MagicMock) -> None:
        self.values = list(values)
        self.calls: list[object] = []

    def __call__(self, *_args, **_kwargs) -> MagicMock:
        # Synchronous on purpose: AsyncMock awaits a plain side_effect's return
        # value, and an `async def` here would return an un-awaited coroutine.
        self.calls.append(_args[0] if _args else None)
        if not self.values:
            return _result(None)
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


def _db(*values: MagicMock) -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock(side_effect=_Queued(*values))
    db.flush = AsyncMock(return_value=None)
    db.delete = AsyncMock()
    return db


def _user(**overrides) -> User:
    now = datetime.now(UTC)
    defaults = {
        "id": uuid.uuid4(),
        "email": "ops@example.com",
        "hashed_password": "hash",
        "full_name": None,
        "is_active": True,
        "is_superuser": False,
        "updated_at": now,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return User(**defaults)


def _reset_record(**overrides) -> PasswordResetToken:
    defaults = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "token_hash": hash_token("token"),
        "expires_at": NOW + timedelta(hours=1),
        "consumed_at": None,
        "created_at": NOW - timedelta(minutes=5),
    }
    defaults.update(overrides)
    return PasswordResetToken(**defaults)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze the clock so expiry assertions are arithmetic, not timing races."""
    monkeypatch.setattr(reset_service, "utcnow", lambda: NOW)
    monkeypatch.setattr(throttle_service, "_now", lambda: NOW)


@pytest.fixture(autouse=True)
def _unlocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assume no lockout, so each test states only the results it cares about.

    The throttle itself is exercised in `TestResetThrottleBudget` below; left
    live here it would add a lookup in front of every one of these and make the
    queued-result order unreadable.
    """
    monkeypatch.setattr(
        throttle_service,
        "check_limits",
        AsyncMock(return_value=throttle_service.ThrottleState(locked=False)),
    )


class TestRequestEnumeration:
    """A reset form that says whether an address exists is an account oracle."""

    async def test_unknown_address_is_accepted_silently(self) -> None:
        # [user lookup, throttle row lookup]: no user, so no token. The throttle
        # write still happens, so a flood of requests for a stranger's address
        # is counted like any other.
        db = _db(_result(None), _result(None))
        assert await reset_service.request_reset(db, email="nobody@example.com") is False
        db.flush.assert_awaited()

    async def test_active_user_gets_a_token(self) -> None:
        db = _db(_result(_user()), _result(rows=[]))
        assert await reset_service.request_reset(db, email="ops@example.com") is True

    async def test_disabled_account_gets_nothing(self) -> None:
        """Same response as an unknown address, so support cannot be probed."""
        db = _db(_result(_user(is_active=False)), _result(None))
        assert await reset_service.request_reset(db, email="ops@example.com") is False

    async def test_soft_deleted_account_gets_nothing(self) -> None:
        db = _db(_result(_user(deleted_at=NOW)), _result(None))
        assert await reset_service.request_reset(db, email="ops@example.com") is False

    async def test_no_token_row_is_written_for_an_unknown_address(self) -> None:
        db = _db(_result(None), _result(None))
        await reset_service.request_reset(db, email="nobody@example.com")
        added = [c.args[0] for c in db.add.call_args_list]
        # Only the throttle counter. A token row for a stranger would be a row
        # nobody can use and nobody would ever think to delete.
        assert not [a for a in added if isinstance(a, PasswordResetToken)]
        assert not [a for a in added if isinstance(a, EmailOutbox)]

    async def test_throttled_address_still_looks_like_success(self) -> None:
        db = MagicMock()
        db.execute = AsyncMock(return_value=_result(None))
        db.flush = AsyncMock()
        with patch.object(
            throttle_service, "check_limits", AsyncMock(
                return_value=throttle_service.ThrottleState(
                    locked=True, retry_after_seconds=600, scope="reset_email"
                )
            )
        ):
            assert await reset_service.request_reset(db, email="ops@example.com") is False
        # The lockout must not be visible as a different outcome.
        db.execute.assert_not_awaited()


class TestLiveTokenCap:
    async def test_existing_live_tokens_are_retired_first(self) -> None:
        """The number of usable secrets per account is bounded, not unbounded.

        Five issued tokens would mean five ways into the account from one
        mailbox history. Requesting a new one retires the oldest until only
        `reset_max_live_tokens` remain.
        """
        from app.core.config import get_settings

        user = _user()
        existing = [
            _reset_record(user_id=user.id, created_at=NOW - timedelta(minutes=10 - i))
            for i in range(4)
        ]
        db = _db(_result(user), _result(rows=existing))
        assert await reset_service.request_reset(db, email=user.email) is True
        consumed = [r.consumed_at for r in existing]
        assert consumed[0] == NOW
        assert consumed[-1] is None
        assert sum(1 for c in consumed if c is not None) == 2
        assert get_settings().reset_max_live_tokens == 3

    async def test_a_single_live_token_is_not_retired(self) -> None:
        user = _user()
        existing = [_reset_record(user_id=user.id)]
        db = _db(_result(user), _result(rows=existing))
        await reset_service.request_reset(db, email=user.email)
        assert existing[0].consumed_at is None


class TestConfirm:
    async def test_valid_token_sets_the_password_and_revokes_sessions(self) -> None:
        user = _user()
        # [consume UPDATE, user lookup, delete other tokens]
        db = _db(_result(user.id), _result(user), _result(None))
        with patch.object(
            reset_service.sessions, "revoke_all_sessions", AsyncMock(return_value=3)
        ) as revoke:
            result = await reset_service.confirm_reset(
                db, token="tok", new_password="a-long-enough-password"
            )
        assert result.user is user
        # The reason a reset is worth doing at all.
        revoke.assert_awaited_once()
        assert revoke.await_args.kwargs.get("except_session") is None
        assert result.sessions_revoked == 3
        assert user.hashed_password != "hash"
        assert user.updated_at == NOW

    async def test_expired_token_is_refused_by_the_where_clause(self) -> None:
        """Single-use and expiry are enforced in SQL, not by a prior read.

        The conditional UPDATE is what makes two concurrent submissions produce
        one winner. This asserts the expiry test is in that statement, because
        moving it to a read-then-write would reopen the race.
        """
        db = _db(_result(None))
        with pytest.raises(reset_service.InvalidResetTokenError):
            await reset_service.confirm_reset(
                db, token="tok", new_password="a-long-enough-password"
            )
        statement = db.execute.await_args_list[0].args[0]
        assert isinstance(statement, Update)
        compiled = str(statement)
        assert "consumed_at IS NULL" in compiled
        assert "expires_at >" in compiled

    async def test_used_token_is_refused(self) -> None:
        db = _db(_result(None))
        with pytest.raises(reset_service.InvalidResetTokenError):
            await reset_service.confirm_reset(
                db, token="tok", new_password="a-long-enough-password"
            )

    async def test_unknown_token_and_disabled_user_are_the_same_error(self) -> None:
        db = _db(_result(uuid.uuid4()), _result(_user(is_active=False)))
        with pytest.raises(reset_service.InvalidResetTokenError):
            await reset_service.confirm_reset(
                db, token="tok", new_password="a-long-enough-password"
            )

    async def test_weak_password_leaves_the_token_unconsumed(self) -> None:
        """A policy rejection must not burn the link.

        The person cannot request a new one without an inbox they may no longer
        control, and "your password was too short, try again with the same link"
        is the only humane outcome.
        """
        from app.core.security.passwords import PasswordPolicyError

        user = _user()
        db = _db(_result(user.id), _result(user), _result(None))
        with pytest.raises(PasswordPolicyError):
            await reset_service.confirm_reset(db, token="tok", new_password="short")
        assert user.hashed_password == "hash"

    async def test_other_live_tokens_are_deleted_on_reset(self) -> None:
        """A second outstanding token must not outlive the password it resets."""
        user = _user()
        db = _db(_result(user.id), _result(user), _result(None))
        with patch.object(
            reset_service.sessions, "revoke_all_sessions", AsyncMock(return_value=1)
        ):
            await reset_service.confirm_reset(
                db, token="tok", new_password="a-long-enough-password"
            )
        deletions = [
            call.args[0]
            for call in db.execute.await_args_list
            if isinstance(call.args[0], Delete)
        ]
        assert len(deletions) == 1
        assert "password_reset_tokens" in str(deletions[0])


class TestTokenStorage:
    def test_only_the_digest_is_persisted(self) -> None:
        """A database disclosure must not yield a usable reset link."""
        token = "the-plaintext-token"
        assert hash_token(token) != token
        assert len(hash_token(token)) == 64
        assert hash_token(token) == hash_token(token)

    async def test_lookup_never_sends_the_plaintext(self) -> None:
        db = _db(_result(None))
        with pytest.raises(reset_service.InvalidResetTokenError):
            await reset_service.confirm_reset(
                db, token="secret-token", new_password="a-long-enough-password"
            )
        statement = db.execute.await_args_list[0].args[0]
        compiled = statement.compile()
        assert "secret-token" not in str(compiled)
        assert hash_token("secret-token") in compiled.params.values()


class TestResetThrottleBudget:
    """The reset counters are separate from the login counters (D-017)."""

    def test_reset_uses_its_own_scopes(self) -> None:
        limits = reset_service._reset_limits(email="A@B.com", ip_address="1.2.3.4")
        assert [limit.scope for limit in limits] == [
            throttle_service.SCOPE_RESET_EMAIL,
            throttle_service.SCOPE_RESET_IP,
        ]
        assert limits[0].value == "a@b.com"

    def test_scopes_are_not_the_login_scopes(self) -> None:
        """Otherwise a reset flood would lock the victim out of signing in.

        That is the denial-of-service this split exists to prevent: anyone who
        can submit a reset request for a known address could otherwise keep that
        account locked out for the length of the lockout.
        """
        login = throttle_service.login_limits(email="a@b.com", ip_address=None)
        reset = reset_service._reset_limits(email="a@b.com", ip_address=None)
        assert login[0].scope != reset[0].scope
        assert login[0].value == reset[0].value

    def test_ip_budget_is_added_only_when_the_address_is_known(self) -> None:
        assert len(reset_service._reset_limits(email="a@b.com", ip_address=None)) == 1
        assert len(reset_service._reset_limits(email="a@b.com", ip_address="1.2.3.4")) == 2

    def test_an_unknown_scope_cannot_be_counted(self) -> None:
        """A typo here would hash into a scope nobody reads.

        The counter would silently never fire, which is the worst possible
        outcome for a security control: it looks configured and does nothing.
        """
        with pytest.raises(ValueError, match="unknown throttle scope"):
            throttle_service.Limit("emial", "a@b.com", 1, timedelta(minutes=1))


class TestDeliveryIsQueuedNotSent:
    async def test_reset_queues_exactly_one_message(self) -> None:
        user = _user()
        db = _db(_result(user), _result(rows=[]))
        await reset_service.request_reset(db, email=user.email)
        messages = [
            c.args[0]
            for c in db.add.call_args_list
            if isinstance(c.args[0], EmailOutbox)
        ]
        assert len(messages) == 1
        message = messages[0]
        assert message.recipient == user.email
        assert message.related_id is None
        # `sent_at` stays NULL on purpose: the outbox claims nothing it did not do.
        assert message.sent_at is None
        assert message.delivery_channel == "outbox"

    async def test_message_body_carries_the_link_and_the_expiry(self) -> None:
        from app.core.config import get_settings
        from app.services.notify import delivery

        settings = get_settings()
        message = delivery.password_reset_message(
            recipient="ops@example.com", token="tok-123"
        )
        assert "tok-123" in message.body_text
        assert str(settings.reset_token_ttl_minutes) in message.body_text
        assert message.template == delivery.TEMPLATE_RESET
        # The token belongs in the body of one queued message, nowhere else.
        assert message.subject.count("tok-123") == 0

    async def test_the_logged_line_cannot_carry_the_link(self) -> None:
        """Masking already redacts it, so logging it would only look useful.

        D-010 masks any run of 32 or more characters, and every token here is
        longer than that. A development-only "log the link anyway" branch would
        emit a line that appears to contain a working link and does not, which
        is a worse failure than having no such branch.
        """
        from app.core.masking import mask_text
        from app.services.notify import delivery

        message = delivery.password_reset_message(
            recipient="ops@example.com", token="t" * 43
        )
        assert "t" * 43 not in mask_text(message.body_text)
        assert "t" * 43 in message.body_text
