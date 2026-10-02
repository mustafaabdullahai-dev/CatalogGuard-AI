"""Throttling (D-014, D-017).

Independent counters, distinguished by `scope`:

  - `email` counts failed logins against one account, defeating guessing.
  - `ip` counts failed logins from one address, defeating a spray.
  - `reset_email` / `reset_ip` do the same for password reset requests.
  - `invite_email` limits how many invitations one address can receive.

Why reset and invitation have their own budgets
    Sharing the login budget would be a denial-of-service lever: anyone who can
    submit a reset request for a victim's address could lock that account out of
    signing in for the length of the lockout. The reset budget therefore counts
    against its own counters, and a flood of reset requests locks the *sender*
    out of requesting more resets rather than locking the recipient out of their
    account.

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
SCOPE_RESET_EMAIL = "reset_email"
SCOPE_RESET_IP = "reset_ip"
SCOPE_INVITE_EMAIL = "invite_email"

ALL_SCOPES = frozenset(
    {
        SCOPE_EMAIL,
        SCOPE_IP,
        SCOPE_RESET_EMAIL,
        SCOPE_RESET_IP,
        SCOPE_INVITE_EMAIL,
    }
)


@dataclass(frozen=True, slots=True)
class ThrottleState:
    """Why a request is currently refused, and for how much longer."""

    locked: bool
    retry_after_seconds: int = 0
    scope: str | None = None


@dataclass(frozen=True, slots=True)
class Limit:
    """One counter to consult or increment.

    `max_failures` and `lockout` are per-limit rather than global so a reset
    request can be allowed a looser budget than a login attempt: reset issues no
    credential to an attacker, while a login attempt is a guess at one.
    """

    scope: str
    value: str
    max_failures: int
    lockout: timedelta

    def __post_init__(self) -> None:
        if self.scope not in ALL_SCOPES:
            # A typo here would hash into a scope nobody ever reads, so the
            # counter would silently never fire.
            raise ValueError(f"unknown throttle scope: {self.scope}")
        if self.max_failures < 1:
            raise ValueError("max_failures must be at least 1")


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


def login_limits(*, email: str, ip_address: str | None) -> list[Limit]:
    """The two counters a sign-in attempt moves: the account, and the network."""
    settings = get_settings()
    limits = [
        Limit(
            SCOPE_EMAIL,
            normalize_email(email),
            settings.login_max_failures,
            timedelta(minutes=settings.login_lockout_minutes),
        )
    ]
    if ip_address:
        limits.append(
            Limit(
                SCOPE_IP,
                ip_address,
                settings.login_ip_max_failures,
                timedelta(minutes=settings.login_lockout_minutes),
            )
        )
    return limits


async def check_limits(db: AsyncSession, limits: list[Limit]) -> ThrottleState:
    """Return the first lockout that applies among `limits`."""
    now = _now()
    for limit in limits:
        record = await _load(db, limit.scope, limit.value)
        if record is None or record.locked_until is None:
            continue
        if record.locked_until <= now:
            continue
        return ThrottleState(
            locked=True,
            retry_after_seconds=int((record.locked_until - now).total_seconds()) + 1,
            scope=limit.scope,
        )
    return ThrottleState(locked=False)


async def record_limits(
    db: AsyncSession, limits: list[Limit], *, window: timedelta
) -> ThrottleState:
    """Count one failure against each counter and lock whichever hits its limit.

    `window` is passed in rather than read from settings because the reset
    window is deliberately shorter than the login window: a forgotten password
    should become requestable again quickly, while five guessed passwords over a
    long afternoon should still trip the lockout.
    """
    now = _now()
    locked = ThrottleState(locked=False)

    for limit in limits:
        record = await _load(db, limit.scope, limit.value)
        if record is None:
            record = LoginThrottle(
                scope=limit.scope,
                key=_key(limit.scope, limit.value),
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

        if record.failures >= limit.max_failures:
            until = now + limit.lockout
            record.locked_until = until
            locked = ThrottleState(
                locked=True,
                retry_after_seconds=int(limit.lockout.total_seconds()),
                scope=limit.scope,
            )

    await db.flush()
    return locked


async def clear_limits(db: AsyncSession, limits: list[Limit]) -> None:
    """Reset counters after a success. Only ever on success."""
    for limit in limits:
        record = await _load(db, limit.scope, limit.value)
        if record is not None:
            # AsyncSession.delete is a coroutine; a synchronous call here would
            # leave the row queued but never issued.
            await db.delete(record)
    await db.flush()


async def check(
    db: AsyncSession, *, email: str, ip_address: str | None
) -> ThrottleState:
    """Return the lockout that applies to a sign-in, if any.

    Called before the password is checked. A locked account still pays the
    Argon2 cost only if the caller proceeds to verify; refusing early is
    intentional, because a locked-out address should not be able to keep the
    CPU busy either.
    """
    return await check_limits(db, login_limits(email=email, ip_address=ip_address))


async def record_failure(
    db: AsyncSession,
    *,
    email: str,
    ip_address: str | None,
) -> ThrottleState:
    """Count one failed sign-in against the account and network counters."""
    settings = get_settings()
    return await record_limits(
        db,
        login_limits(email=email, ip_address=ip_address),
        window=timedelta(minutes=settings.login_window_minutes),
    )


async def clear(db: AsyncSession, *, email: str, ip_address: str | None) -> None:
    """Reset both counters after a successful login.

    Only on success. Clearing on failure would hand the attacker the budget back
    every attempt, which is a counter that never reaches its limit.
    """
    await clear_limits(db, login_limits(email=email, ip_address=ip_address))
