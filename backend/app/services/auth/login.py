"""Authentication flow (D-014).

Sign-in is deliberately boring: look up the user, check the throttle, verify
the password, open a session, clear the counters. The security properties live
in the ordering of those steps rather than in any one of them.

Ordering rules
    1. Throttle before lookup, so a locked account cannot be used to burn CPU.
    2. Hashing work is constant whether or not the address exists. Otherwise
       response time enumerates who has an account.
    3. The same 401 for "no such user", "wrong password", and "disabled". Any
       difference tells an attacker which guess was closest.
    4. Verify first, then check `is_active`. Checking status first would leak
       whether a disabled account exists and matched.

These functions take an `AsyncSession` rather than opening one, so the caller
owns the transaction. That matters at login specifically: a failure must roll
back the throttle counter just as it rolls back everything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.passwords import (
    dummy_verify,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.models.tenancy import User, WorkspaceMembership
from app.services.auth import sessions, throttle


class AuthenticationError(Exception):
    """Wrong address, wrong password, disabled account, or locked out.

    One exception for all of them. The caller turns it into a single 401 and
    writes a security log entry that records the real reason server-side.
    """


@dataclass(frozen=True, slots=True)
class LoginResult:
    user: User
    session: sessions.IssuedSession
    # True when the password was rehashed mid-request, so the caller can log
    # that the stored cost parameters were upgraded.
    rehashed: bool


def _now() -> datetime:
    return datetime.now(UTC)


async def authenticate(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> LoginResult:
    """Verify credentials and open a session.

    Raises `AuthenticationFailed` for every failure. Callers must not branch on
    the reason.
    """
    normalized = throttle.normalize_email(email)

    state = await throttle.check(db, email=normalized, ip_address=ip_address)
    if state.locked:
        raise AuthenticationError

    result = await db.execute(
        select(User).where(User.email == normalized).limit(1)
    )
    user = result.scalar_one_or_none()

    if user is None:
        # Spend the same CPU a real verification would, so an unknown address
        # and a wrong password are indistinguishable by response time.
        dummy_verify()
        await throttle.record_failure(
            db, email=normalized, ip_address=ip_address
        )
        raise AuthenticationError

    if not verify_password(user.hashed_password, password):
        await throttle.record_failure(
            db, email=normalized, ip_address=ip_address
        )
        raise AuthenticationError

    # Past this point the password is known to be correct, so these checks
    # cannot be used as an oracle for anything an attacker does not already hold.
    if not user.is_active or user.deleted_at is not None:
        await throttle.record_failure(
            db, email=normalized, ip_address=ip_address
        )
        raise AuthenticationError

    rehashed = False
    if needs_rehash(user.hashed_password):
        # Upgrade the stored cost parameters while the plaintext is available.
        # Only reached by someone who just proved they know the password.
        user.hashed_password = hash_password(password)
        rehashed = True

    issued = await sessions.create_session(
        db,
        user=user,
        password_changed_at=user.updated_at,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    await throttle.clear(db, email=normalized, ip_address=ip_address)
    return LoginResult(user=user, session=issued, rehashed=rehashed)


async def change_password(
    db: AsyncSession,
    *,
    user: User,
    current_password: str,
    new_password: str,
    current_session: sessions.AuthSession | None = None,
) -> None:
    """Change a password and revoke every other session.

    The current password is required even though the caller is already
    authenticated: a stolen session cookie should not be enough to lock the real
    owner out of their own account.

    `current_session` is preserved so the caller stays signed in; every other
    device is revoked.
    """
    if not verify_password(user.hashed_password, current_password):
        raise AuthenticationError

    user.hashed_password = hash_password(new_password)
    user.updated_at = _now()
    await sessions.revoke_all_sessions(db, user, except_session=current_session)


async def list_memberships(db: AsyncSession, user: User) -> list[WorkspaceMembership]:
    """Workspaces this user may act in.

    Returned on login and by `/auth/me` so the client never has to guess which
    tenant header to send.
    """
    result = await db.execute(
        select(WorkspaceMembership)
        .where(WorkspaceMembership.user_id == user.id)
        .order_by(WorkspaceMembership.created_at)
    )
    return list(result.scalars())
