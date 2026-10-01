"""Login throttling (D-014).

Two independent counters per attempt:

  - `email`, to slow guessing at one account
  - `ip`, to slow one address spraying many accounts

Both are needed. The email counter alone cannot see a spray; the IP counter
alone punishes a shared office or NAT gateway for one person's mistake.

Why the key is hashed
    An email address is personal data, and this table exists only to be counted
    against. Hashing means a dump of `login_throttles` reveals no addresses and
    no IPs, and the row cannot be joined against `users` without a guess.

Why it is in the database rather than Redis
    A lockout is a security control, and Redis is a cache that can be evicted or
    flushed. Losing the counter should not hand an attacker a fresh budget of
    attempts. It is a tiny, hot table: one indexed row per key.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.tenancy import LoginThrottle

SCOPE_EMAIL = "email"
SCOPE_IP = "ip"


@dataclass(frozen=True, slots=True)
class ThrottleState:
    """Why a login is currently refused, and for how much longer."""

    locked: bool
    retry_after_seconds: int = 0
    scope: str | None = None


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _key(scope: str, value: str) -> str:
    return hashlib.sha256(f"{scope}:{value}".encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


async def _load(db: AsyncSession, scope: str, value: str) -> LoginThrottle | None:
    result = await db.execute(
        select(LoginThrottle).where(
            LoginThrottle.scope == scope, LoginThrottle.key == _key(scope, value)
        )
    )
    return result.scalar_one_or_none()


async def check(
    db: AsyncSession, *, email: str, ip_address: str | None
) -> ThrottleState:
    """Return the lockout that applies, if any.

    Called before the password is checked. A locked account still pays the
    Argon2 cost only if the caller proceeds to verify; refusing early is
    intentional, because a locked-out address should not be able to keep the
    CPU busy either.
    """
    now = _now()
    subjects: list[tuple[str, str]] = [(SCOPE_EMAIL, normalize_email(email))]
    if ip_address:
        subjects.append((SCOPE_IP, ip_address))

    for scope, value in subjects:
        record = await _load(db, scope, value)
        if record is None or record.locked_until is None:
            continue
        if record.locked_until <= now:
            continue
        return ThrottleState(
            locked=True,
            retry_after_seconds=int((record.locked_until - now).total_seconds()) + 1,
            scope=scope,
        )
    return ThrottleState(locked=False)


async def record_failure(
    db: AsyncSession,
    *,
    email: str,
    ip_address: str | None,
) -> ThrottleState:
    """Count one failure against both scopes and lock whichever hits its limit."""
    settings = get_settings()
    now = _now()
    window = timedelta(minutes=settings.login_window_minutes)
    locked = ThrottleState(locked=False)

    subjects: list[tuple[str, str, int]] = [
        (SCOPE_EMAIL, normalize_email(email), settings.login_max_failures)
    ]
    if ip_address:
        subjects.append((SCOPE_IP, ip_address, settings.login_ip_max_failures))

    for scope, value, limit in subjects:
        record = await _load(db, scope, value)
        if record is None:
            record = LoginThrottle(
                scope=scope,
                key=_key(scope, value),
                failures=0,
                window_started_at=now,
            )
            db.add(record)
        elif now - record.window_started_at > window:
            # The window expired, so the old failures do not carry over. Without
            # this, five failures a year apart would be a permanent lockout.
            record.failures = 0
            record.window_started_at = now
            record.locked_until = None

        record.failures += 1
        record.updated_at = now

        if record.failures >= limit:
            until = now + timedelta(minutes=settings.login_lockout_minutes)
            record.locked_until = until
            locked = ThrottleState(
                locked=True,
                retry_after_seconds=settings.login_lockout_minutes * 60,
                scope=scope,
            )

    await db.flush()
    return locked


async def clear(db: AsyncSession, *, email: str, ip_address: str | None) -> None:
    """Reset both counters after a successful login.

    Only on success. Clearing on failure would hand the attacker the budget back
    every attempt, which is a counter that never reaches its limit.
    """
    subjects: list[tuple[str, str]] = [(SCOPE_EMAIL, normalize_email(email))]
    if ip_address:
        subjects.append((SCOPE_IP, ip_address))
    for scope, value in subjects:
        record = await _load(db, scope, value)
        if record is not None:
            # AsyncSession.delete is a coroutine; a synchronous call here would
            # leave the row queued but never issued.
            await db.delete(record)
    await db.flush()
