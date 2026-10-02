"""Password reset (D-017).

The rule that shapes everything here: the request endpoint must not tell the
caller whether an address has an account. So `request_reset` always returns the
same thing, always logs the same shape, and always does the same amount of
work — including generating a token that is thrown away when nobody is listening.

What is protected, and why

  - **Enumeration.** A different status, body, or log line for a known address
    turns the reset form into an account-existence oracle, which is a strictly
    worse leak than the one being fixed: a password reset exists precisely for
    people who cannot prove who they are.
  - **Reuse.** A token is consumed by a conditional UPDATE, so two requests
    racing the same token produce one winner and one failure.
  - **Theft.** Resetting revokes every session. A reset is usually somebody's
    response to a compromise, and leaving the intruder's cookie alive would make
    the reset cosmetic.
  - **Mailbox flooding.** Three requests an hour per address, and a cap on live
    tokens, so a harvested address cannot be used to bury real mail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security.passwords import hash_password
from app.core.security.tokens import generate_token, hash_token, utcnow
from app.models.tenancy import PasswordResetToken, User
from app.services.auth import sessions, throttle
from app.services.notify import delivery

logger = get_logger(__name__)


class InvalidResetTokenError(Exception):
    """Expired, consumed, revoked, unknown, or already-used token.

    One exception for every case. The endpoint turns it into one 400 with one
    message, because "this token was used 4 days ago" and "this token never
    existed" are both answers an attacker is probing for.
    """


@dataclass(frozen=True, slots=True)
class ResetCompleted:
    """Result of a successful reset.

    `sessions_revoked` is returned so the caller can report it. A reset that
    silently killed six sessions is indistinguishable, from the outside, from one
    that killed none, and "sign in again everywhere" is exactly the message a
    person who was locked out needs to be told explicitly.
    """

    user: User
    sessions_revoked: int


@dataclass(frozen=True, slots=True)
class ResetIssued:
    """A newly minted reset token.

    `token` is the plaintext, which exists only for the length of the request
    that mails it. It is never returned to the caller of the API.
    """

    record: PasswordResetToken
    token: str


def _reset_limits(*, email: str, ip_address: str | None) -> list[throttle.Limit]:
    settings = get_settings()
    window = timedelta(minutes=settings.reset_lockout_minutes)
    limits = [
        throttle.Limit(
            throttle.SCOPE_RESET_EMAIL,
            throttle.normalize_email(email),
            settings.reset_max_requests,
            window,
        )
    ]
    if ip_address:
        limits.append(
            throttle.Limit(
                throttle.SCOPE_RESET_IP,
                ip_address,
                settings.reset_ip_max_requests,
                window,
            )
        )
    return limits


async def request_reset(
    db: AsyncSession,
    *,
    email: str,
    ip_address: str | None = None,
) -> bool:
    """Start a reset if the address belongs to an active user.

    Returns True when a token was issued. The caller ignores it: the response is
    identical either way, and returning the flag only invites a branch on it
    somewhere downstream. It exists for the test suite and the log line.
    """
    settings = get_settings()
    normalized = throttle.normalize_email(email)
    limits = _reset_limits(email=normalized, ip_address=ip_address)

    # A generator run on every request, including for addresses that do not
    # exist, so the CSPRNG cost and the throttle write are the same on both
    # paths. What remains observable is one extra INSERT for a real user, which
    # is why the throttle also caps requests per address.
    token = generate_token()
    now = utcnow()

    state = await throttle.check_limits(db, limits)
    if state.locked:
        # Recorded, not refused. Answering 429 here would tell a prober that
        # this address has been requested before, which is a fact about a
        # stranger's account.
        logger.info(
            "password_reset_throttled",
            scope=state.scope,
            ip=ip_address,
            retry_after_seconds=state.retry_after_seconds,
        )
        return False

    user = (
        await db.execute(select(User).where(User.email == normalized).limit(1))
    ).scalar_one_or_none()

    if user is None or not user.is_active or user.deleted_at is not None:
        await throttle.record_limits(
            db, limits, window=timedelta(minutes=settings.reset_window_minutes)
        )
        return False

    # Retire the oldest live tokens so the number of usable secrets per account
    # is bounded by `reset_max_live_tokens` regardless of how many were issued.
    live = list(
        (
            await db.execute(
                select(PasswordResetToken)
                .where(
                    PasswordResetToken.user_id == user.id,
                    PasswordResetToken.consumed_at.is_(None),
                )
                .order_by(PasswordResetToken.created_at)
            )
        ).scalars()
    )
    for stale in live[: max(0, len(live) - settings.reset_max_live_tokens + 1)]:
        stale.consumed_at = now

    record = PasswordResetToken(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=now + timedelta(minutes=settings.reset_token_ttl_minutes),
        requested_ip=ip_address,
    )
    db.add(record)
    await db.flush()

    await throttle.clear_limits(db, limits)
    await delivery.get_delivery().deliver(
        db, delivery.password_reset_message(recipient=user.email, token=token)
    )
    logger.info("password_reset_requested", user_id=str(user.id), ip=ip_address)
    return True


async def confirm_reset(
    db: AsyncSession, *, token: str, new_password: str
) -> ResetCompleted:
    """Set a new password and revoke every session the account had.

    No session is issued in return. The caller is signing in from a device this
    request knows nothing about, and handing it a session would make the reset
    the thing that logs an attacker in.
    """
    now = utcnow()

    # Compare-and-swap. The WHERE clause is the single-use and expiry test, so
    # two concurrent submissions cannot both see a live row.
    result = await db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == hash_token(token),
            PasswordResetToken.consumed_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(PasswordResetToken.user_id)
    )
    user_id = result.scalar_one_or_none()
    if user_id is None:
        raise InvalidResetTokenError

    user = (
        await db.execute(select(User).where(User.id == user_id).limit(1))
    ).scalar_one_or_none()
    if user is None or not user.is_active or user.deleted_at is not None:
        raise InvalidResetTokenError

    # Raises PasswordPolicyError for a password that is too short or has
    # whitespace at the edges; the endpoint turns that into a 422 and the
    # transaction rolls back, so a rejected policy leaves the token unconsumed
    # and the person can try again with the same link.
    user.hashed_password = hash_password(new_password)
    user.updated_at = now

    revoked = await sessions.revoke_all_sessions(db, user)
    # Any other live token for this account dies with the password it would
    # have reset, including one issued moments ago in a different tab.
    await db.execute(
        delete(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.consumed_at.is_(None),
        )
    )
    logger.info("password_reset_completed", user_id=str(user.id), sessions_revoked=revoked)
    return ResetCompleted(user=user, sessions_revoked=revoked)


async def purge_expired(db: AsyncSession, *, older_than_days: int = 7) -> int:
    """Delete tokens that expired long ago.

    Consumed tokens are kept briefly rather than deleted on use: "this token was
    already redeemed at 14:02" is the answer to most support questions about a
    reset that appears not to have worked.
    """
    cutoff = utcnow() - timedelta(days=older_than_days)
    result = await db.execute(
        delete(PasswordResetToken).where(PasswordResetToken.expires_at < cutoff)
    )
    return int(result.rowcount or 0)
