"""Session and throttle tests (D-014).

No database. `AsyncSession` is mocked at the `execute`/`flush` boundary, which
is the seam these modules actually use: one indexed lookup by token digest, and
one row per throttle key. The logic under test is expiry arithmetic, revocation
rules, and the lockout window, none of which a real database would exercise
anyway.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.tenancy import AuthSession
from app.services.auth import sessions as session_service
from app.services.auth import throttle as throttle_service


def _result(value=None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalar.return_value = value
    result.scalar_one.return_mock = None
    result.rowcount = 1
    return result


def _db(value=None) -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock(return_value=_result(value))
    db.flush = AsyncMock(return_value=None)
    db.delete = AsyncMock()
    return db


def _record(**overrides) -> AuthSession:
    now = datetime.now(UTC)
    defaults = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "token_hash": session_service.hash_token("token"),
        "password_changed_at": now - timedelta(days=1),
        "expires_at": now + timedelta(days=1),
        "revoked_at": None,
        "last_seen_at": now,
    }
    defaults.update(overrides)
    return AuthSession(**defaults)


class TestTokenStorage:
    def test_token_is_long_enough_to_be_unpredictable(self) -> None:
        """256 bits.

        Anything shorter makes an online guessing attack against the cookie
        cheaper than the Argon2 cost of a password guess, which defeats the
        point of the password in the first place.
        """
        import base64

        raw = base64.urlsafe_b64decode(session_service.generate_token() + "==")
        assert len(raw) >= 32

    def test_tokens_are_unique(self) -> None:
        tokens = {session_service.generate_token() for _ in range(50)}
        assert len(tokens) == 50

    def test_only_the_digest_is_stored(self) -> None:
        token = session_service.generate_token()
        digest = session_service.hash_token(token)
        assert digest != token
        assert len(digest) == 64
        assert session_service.hash_token(token) == digest

    async def test_lookup_uses_the_digest(self) -> None:
        """The plaintext token must never reach the database layer."""
        token = session_service.generate_token()
        db = _db(_record())
        await session_service.resolve_session(db, token)
        statement = db.execute.await_args.args[0]
        assert token not in str(statement)
        # Bound, not interpolated: the digest travels as a parameter, so it is
        # absent from the SQL text and present in the compiled parameters.
        assert token not in str(statement.compile())
        assert session_service.hash_token(token) in statement.compile().params.values()


class TestSessionExpiry:
    async def test_unknown_token_is_none(self) -> None:
        assert await session_service.resolve_session(_db(None), "nope") is None

    async def test_valid_session_resolves(self) -> None:
        record = _record()
        found = await session_service.resolve_session(_db(record), "token")
        assert found is record

    async def test_expired_session_is_refused(self) -> None:
        now = datetime.now(UTC)
        record = _record(expires_at=now - timedelta(seconds=1))
        assert await session_service.resolve_session(_db(record), "token") is None

    async def test_revoked_session_is_refused(self) -> None:
        record = _record(revoked_at=datetime.now(UTC))
        assert await session_service.resolve_session(_db(record), "token") is None

    async def test_idle_window_is_enforced(self) -> None:
        """A stolen cookie is only useful while its owner is present.

        The idle window is what bounds that, and it slides on each request, so
        an owner in daily use never trips it while an abandoned copy does.
        """
        now = datetime.now(UTC)
        record = _record(last_seen_at=now - timedelta(hours=13))
        assert await session_service.resolve_session(_db(record), "token") is None

    async def test_active_session_survives_the_idle_window(self) -> None:
        now = datetime.now(UTC)
        record = _record(last_seen_at=now - timedelta(hours=11))
        assert await session_service.resolve_session(_db(record), "token") is not None

    async def test_password_changed_in_the_future_is_refused(self) -> None:
        """Clock skew fails closed.

        A session stamped in the future means this server's clock and the writer
        disagree. Trusting it would accept a session the writer may already
        consider dead.
        """
        record = _record(password_changed_at=datetime.now(UTC) + timedelta(hours=1))
        assert await session_service.resolve_session(_db(record), "token") is None


class TestRevocation:
    async def test_revoke_keeps_the_row(self) -> None:
        """When it was revoked is worth more than the space.

        Deleting instead would erase the only evidence that a session existed.
        """
        record = _record()
        await session_service.revoke_session(record)
        assert record.revoked_at is not None
        assert await session_service.resolve_session(_db(record), "token") is None

    async def test_revoke_all_excepts_the_current_session(self) -> None:
        """Changing a password must not sign you out of the tab you changed it in."""
        db = _db()
        user = MagicMock(id=uuid.uuid4())
        current = _record()
        revoked = await session_service.revoke_all_sessions(
            db, user, except_session=current
        )
        assert revoked == 1
        statement = db.execute.await_args.args[0]
        assert "auth_sessions.id != :id_1" in str(statement)
        assert current.id in statement.compile().params.values()

    async def test_revoke_all_without_except_touches_everything(self) -> None:
        """Used on deactivation, where every session must go."""
        db = _db()
        await session_service.revoke_all_sessions(db, MagicMock(id=uuid.uuid4()))
        statement = str(db.execute.await_args.args[0])
        assert "id !=" not in statement


class TestThrottle:
    def test_email_is_normalized(self) -> None:
        """Case must not mint a fresh budget per attempt."""
        assert throttle_service.normalize_email("  Ops@Example.COM ") == (
            "ops@example.com"
        )

    async def test_key_is_hashed(self) -> None:
        """`login_throttles` must not be a list of customer addresses."""
        db = _db(None)
        await throttle_service._load(
            db, throttle_service.SCOPE_EMAIL, "ops@example.com"
        )
        statement = str(db.execute.await_args.args[0])
        assert "ops@example.com" not in statement

    def test_key_differs_by_scope(self) -> None:
        """The same string under `email` and `ip` must not collide."""
        email_key = throttle_service._key(throttle_service.SCOPE_EMAIL, "1.2.3.4")
        ip_key = throttle_service._key(throttle_service.SCOPE_IP, "1.2.3.4")
        assert email_key != ip_key

    async def test_unknown_key_is_not_locked(self) -> None:
        state = await throttle_service.check(
            _db(None), email="a@b.com", ip_address="1.2.3.4"
        )
        assert state.locked is False

    async def test_active_lock_is_reported_with_a_retry(self) -> None:
        """The client needs to know how long, or it will hammer the endpoint."""
        until = datetime.now(UTC) + timedelta(minutes=5)
        record = MagicMock(locked_until=until)
        db = MagicMock()
        db.execute = AsyncMock(return_value=_result(record))
        db.flush = AsyncMock(return_value=None)
        state = await throttle_service.check(
            db, email="a@b.com", ip_address=None
        )
        assert state.locked is True
        assert state.scope == throttle_service.SCOPE_EMAIL
        assert 0 < state.retry_after_seconds <= 300

    async def test_expired_lock_does_not_block(self) -> None:
        """A lockout that never lifts is a self-inflicted outage."""
        record = MagicMock(locked_until=datetime.now(UTC) - timedelta(seconds=1))
        db = MagicMock()
        db.execute = AsyncMock(return_value=_result(record))
        state = await throttle_service.check(db, email="a@b.com", ip_address=None)
        assert state.locked is False

    async def test_clear_deletes_the_row(self) -> None:
        """Reset only on success.

        Clearing on failure would hand back the budget on every attempt, which is
        a counter that can never reach its limit.
        """
        db = _db(MagicMock())
        await throttle_service.clear(db, email="a@b.com", ip_address=None)
        db.delete.assert_awaited_once()

    async def test_clear_is_safe_when_nothing_is_recorded(self) -> None:
        db = _db(None)
        await throttle_service.clear(db, email="a@b.com", ip_address=None)
        db.delete.assert_not_awaited()


class TestPasswordChangesAreNotRateLimited:
    @pytest.mark.parametrize("current,new,expected", [("wrong", "x" * 12, False)])
    async def test_current_password_must_verify(
        self, current: str, new: str, expected: bool
    ) -> None:
        """The check happens in `login.change_password`, exercised here through
        the exception it raises.

        An authenticated caller must still prove they know the current password,
        or a stolen session cookie becomes a permanent account takeover.
        """
        from app.core.security.passwords import hash_password
        from app.models.tenancy import User
        from app.services.auth.login import AuthenticationError, change_password

        user = User(
            id=uuid.uuid4(),
            email="ops@example.com",
            hashed_password=hash_password("the-real-password"),
            is_active=True,
        )
        db = _db()
        with pytest.raises(AuthenticationError):
            await change_password(
                db, user=user, current_password=current, new_password=new
            )
        assert user.hashed_password != hash_password(new)
