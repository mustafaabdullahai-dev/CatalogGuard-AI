"""Server-side sessions (D-014).

Why not a JWT in the cookie
    Revocation. Approval authority is the thing this system protects, so a
    password reset, a removed membership, or a suspected theft must take effect
    immediately. A self-contained JWT stays valid until it expires no matter
    what the server now believes, which means "log out everywhere" is not
    expressible. The session table makes revocation a single row.

What is stored
    The cookie carries an opaque 256-bit token. Only its SHA-256 digest is
    stored, so a read of the database does not yield a usable cookie, and a
    lookup by digest is a single indexed equality check.

Two expiries
    Idle: 12 hours of inactivity ends the session, bounding the value of a
    stolen cookie. Absolute: 14 days, so a session cannot be kept alive
    indefinitely by periodic requests.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.tenancy import AuthSession, User

TOKEN_BYTES = 32


def hash_token(token: str) -> str:
    """Digest a cookie token for storage and lookup.

    SHA-256, not a password hash. The input is 256 bits of CSPRNG output, so
    there is no dictionary to attack and no need to spend Argon2 time on the
    hot path.
    """
    return hashlib.sha256(token.encode()).hexdigest()


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A new session: the row to persist and the token to put in the cookie.

    They are returned together deliberately so the plaintext token exists in
    exactly one place, at the moment it is first needed.
    """

    record: AuthSession
    token: str


def _now() -> datetime:
    return datetime.now(UTC)


async def create_session(
    db: AsyncSession,
    *,
    user: User,
    password_changed_at: datetime,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedSession:
    settings = get_settings()
    token = generate_token()
    record = AuthSession(
        user_id=user.id,
        token_hash=hash_token(token),
        # Stamped rather than read from the user at validation time: a password
        # change invalidates only the sessions that predate it, so "log out
        # everywhere" and "keep my other devices" are both expressible.
        password_changed_at=password_changed_at,
        expires_at=_now() + timedelta(hours=settings.session_absolute_timeout_hours),
        user_agent=user_agent,
        ip_address=ip_address,
        last_seen_at=_now(),
    )
    db.add(record)
    await db.flush()
    return IssuedSession(record=record, token=token)


async def resolve_session(db: AsyncSession, token: str) -> AuthSession | None:
    """Look up a cookie token and return its session if it is still usable.

    Returns None for every failure mode. The caller answers 401 uniformly,
    because distinguishing "expired" from "revoked" from "no such token"
    tells an attacker which part of the guess was right.
    """
    settings = get_settings()
    now = _now()
    result = await db.execute(
        select(AuthSession).where(AuthSession.token_hash == hash_token(token))
    )
    record = result.scalar_one_or_none()
    if record is None:
        return None
    if record.revoked_at is not None or record.expires_at <= now:
        return None
    if record.last_seen_at is not None:
        idle_deadline = record.last_seen_at + timedelta(
            minutes=settings.session_idle_timeout_minutes
        )
        if idle_deadline <= now:
            return None
    if record.password_changed_at > now:
        # Clock skew or a bad write. Fail closed rather than trust it.
        return None
    return record


async def touch_session(record: AuthSession) -> None:
    """Record activity so the idle window slides.

    Takes the record rather than the session deliberately. The caller owns the
    transaction and will flush or commit as part of its own work, so this adds
    no query of its own.
    """
    record.last_seen_at = _now()


async def revoke_session(record: AuthSession) -> None:
    """Mark one session dead.

    Keeps the row rather than deleting it: when is a session revoked is worth
    more than the space it occupies.
    """
    record.revoked_at = _now()


async def revoke_all_sessions(
    db: AsyncSession,
    user: User,
    *,
    except_session: AuthSession | None = None,
) -> int:
    """Revoke every live session for a user, optionally keeping one.

    Used on password change and on deactivation. `except_session` is the caller's
    current session, so changing a password does not log you out of the tab you
    changed it in.
    """
    now = _now()
    statement = (
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    if except_session is not None:
        statement = statement.where(AuthSession.id != except_session.id)
    result = await db.execute(statement)
    return int(result.rowcount or 0)


async def purge_expired(db: AsyncSession, *, older_than_days: int = 30) -> int:
    """Delete sessions that expired long ago.

    Revoked rows stay for a while on purpose: "this session was revoked at this
    time" is worth keeping as a security signal, and a row that expires and is
    deleted in the same instant carries no history.
    """
    cutoff = _now() - timedelta(days=older_than_days)
    result = await db.execute(
        delete(AuthSession).where(AuthSession.expires_at < cutoff)
    )
    return int(result.rowcount or 0)
