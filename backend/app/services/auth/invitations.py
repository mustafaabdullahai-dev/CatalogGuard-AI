"""Workspace invitations (D-017).

An invitation is a bearer secret that grants a role. Three things follow from
that, and they are the design.

The role is bound at issue time
    The accepting caller never names a role. `role` is read from the row and
    copied onto the membership, so a tampered request body cannot grant
    `ecommerce_ops_manager` to someone invited as a merchandiser. This is the
    difference between an invitation and an open signup endpoint.

One live invitation per person per workspace
    Resending replaces the token on the existing row rather than adding a
    second. Two live rows for one address would mean revoking one and still
    having access granted by the other, which is exactly the failure a support
    team cannot diagnose.

An existing account keeps its password
    Accepting an invitation for an address that already has an account creates
    the membership and leaves the credential alone. The person chose that
    password; making them set it again would be an account takeover dressed up
    as onboarding. It also means an invitation cannot be used to reset a
    password someone else chose.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security.tokens import generate_token, hash_token, utcnow
from app.models.tenancy import (
    User,
    UserRole,
    Workspace,
    WorkspaceInvitation,
    WorkspaceMembership,
)
from app.services.auth import throttle
from app.services.notify import delivery

logger = get_logger(__name__)


class InvitationError(Exception):
    """An invitation cannot be issued or accepted. The endpoint maps each to 4xx."""


class InvitationNotFoundError(InvitationError):
    """Unknown, expired, revoked, or already-accepted token.

    Indistinguishable from the caller's point of view, and deliberately so.
    """


class InvitationAlreadySentError(InvitationError):
    """A live invitation already exists for this address in this workspace.

    Carries the existing record so the caller can resend rather than fail: the
    common cause is an admin who hit "resend" twice, and the useful response is
    a new token, not an error the operator has to reason about.
    """


class InvitationRecipientInvalidError(InvitationError):
    """The address already belongs to this workspace, or its account is disabled."""


@dataclass(frozen=True, slots=True)
class IssuedInvitation:
    """The row to persist, the plaintext for the message, and the message id.

    `queued_message_id` comes from the delivery port, so it is whatever transport
    actually accepted the message. A future SMTP adapter returns its provider
    message id and no caller changes.
    """

    record: WorkspaceInvitation
    token: str
    queued_message_id: object


def _live_filters() -> tuple[object, object]:
    """Predicates for an invitation that can still be redeemed."""
    return (
        WorkspaceInvitation.revoked_at.is_(None),
        WorkspaceInvitation.accepted_at.is_(None),
    )


async def _find_live(
    db: AsyncSession, *, workspace_id: object, email: str
) -> WorkspaceInvitation | None:
    result = await db.execute(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.workspace_id == workspace_id,
            WorkspaceInvitation.email == email,
            *_live_filters(),
        )
    )
    return result.scalar_one_or_none()


async def _is_member(db: AsyncSession, *, workspace_id: object, user_id: object) -> bool:
    count = await db.scalar(
        select(func.count())
        .select_from(WorkspaceMembership)
        .where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
        )
    )
    return bool(count)


async def invite(
    db: AsyncSession,
    *,
    workspace_id: object,
    email: str,
    role: UserRole,
    invited_by: User,
    resend: bool = False,
) -> IssuedInvitation:
    """Issue or reissue an invitation.

    `resend` replaces the token on an existing live invitation instead of
    raising. The old token is dead the moment the new one is written, which is
    the point: a resend after a suspected leak is how you actually get rid of the
    leaked one.
    """
    settings = get_settings()
    normalized = throttle.normalize_email(email)
    now = utcnow()

    existing_user = (
        await db.execute(select(User).where(User.email == normalized).limit(1))
    ).scalar_one_or_none()
    if existing_user is not None:
        if not existing_user.is_active or existing_user.deleted_at is not None:
            # A disabled account can be invited only by someone with database
            # access re-enabling it; inviting it here would quietly restore
            # access the operator deliberately removed.
            raise InvitationRecipientInvalidError(
                "That address belongs to a disabled account"
            )
        if await _is_member(
            db, workspace_id=workspace_id, user_id=existing_user.id
        ):
            raise InvitationRecipientInvalidError(
                "That address is already a member of this workspace"
            )

    sent = await db.scalar(
        select(func.count())
        .select_from(WorkspaceInvitation)
        .where(WorkspaceInvitation.email == normalized)
    )
    if sent and sent >= settings.invitation_max_per_recipient:
        raise InvitationRecipientInvalidError(
            "That address has received too many invitations; contact support"
        )

    live = await _find_live(db, workspace_id=workspace_id, email=normalized)
    if live is not None and not resend:
        raise InvitationAlreadySentError(live)

    token = generate_token()
    workspace = (
        await db.execute(
            select(Workspace).where(Workspace.id == workspace_id).limit(1)
        )
    ).scalar_one_or_none()
    if workspace is None:
        raise InvitationError("workspace not found")

    if live is not None:
        live.token_hash = hash_token(token)
        live.expires_at = now + timedelta(hours=settings.invitation_token_ttl_hours)
        live.role = role
        live.invited_by_user_id = invited_by.id
        live.revoked_at = None
        record = live
    else:
        record = WorkspaceInvitation(
            workspace_id=workspace_id,
            email=normalized,
            role=role,
            invited_by_user_id=invited_by.id,
            token_hash=hash_token(token),
            expires_at=now + timedelta(hours=settings.invitation_token_ttl_hours),
        )
        db.add(record)
    await db.flush()

    queued_message_id = await delivery.get_delivery().deliver(
        db,
        delivery.invitation_message(
            recipient=normalized,
            workspace_name=workspace.name,
            role=str(role),
            invited_by=invited_by.full_name or invited_by.email,
            token=token,
        ),
    )
    logger.info(
        "invitation_issued",
        invitation_id=str(record.id),
        workspace_id=str(workspace_id),
        role=str(role),
        invited_by=str(invited_by.id),
        resent=live is not None,
    )
    return IssuedInvitation(
        record=record, token=token, queued_message_id=queued_message_id
    )


async def revoke(db: AsyncSession, *, invitation_id: object, workspace_id: object) -> bool:
    """Revoke a live invitation.

    Scoped by workspace on purpose: revoking by id alone would let an admin of
    one workspace cancel another workspace's invitation by guessing a UUID.
    Returns False when there was nothing live to revoke, which the caller reports
    as 404 for the same reason — a revocation that worked and one that never
    existed should look identical to a prober.
    """
    now = utcnow()
    result = await db.execute(
        update(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.id == invitation_id,
            WorkspaceInvitation.workspace_id == workspace_id,
            *_live_filters(),
        )
        .values(revoked_at=now)
        .returning(WorkspaceInvitation.id)
    )
    return result.scalar_one_or_none() is not None


async def list_invitations(
    db: AsyncSession, *, workspace_id: object, include_closed: bool = False
) -> list[WorkspaceInvitation]:
    conditions = [WorkspaceInvitation.workspace_id == workspace_id]
    if not include_closed:
        conditions.extend(_live_filters())
    result = await db.execute(
        select(WorkspaceInvitation)
        .where(*conditions)
        .order_by(WorkspaceInvitation.created_at.desc())
    )
    return list(result.scalars())


@dataclass(frozen=True, slots=True)
class AcceptedInvitation:
    """What accepting produced: the user, the membership, and whether it is new."""

    user: User
    membership: WorkspaceMembership
    created_user: bool


async def accept(
    db: AsyncSession,
    *,
    token: str,
    password: str | None,
    full_name: str | None,
) -> AcceptedInvitation:
    """Redeem an invitation: create or find the user, then create the membership.

    The role comes from the stored row. Nothing in the request can influence
    it, which is what makes an invitation safe to send by email.

    `password` is required only when the address has no account yet; supplying
    one for an existing account is an error rather than a silent overwrite, so a
    caller cannot use an invitation link to reset somebody else's credential.
    """
    from app.core.security.passwords import hash_password

    now = utcnow()
    result = await db.execute(
        update(WorkspaceInvitation)
        .where(
            WorkspaceInvitation.token_hash == hash_token(token),
            *_live_filters(),
            WorkspaceInvitation.expires_at > now,
        )
        .values(accepted_at=now)
        .returning(WorkspaceInvitation)
    )
    invitation = result.scalar_one_or_none()
    if invitation is None:
        raise InvitationNotFoundError

    user = (
        await db.execute(
            select(User).where(User.email == invitation.email).limit(1)
        )
    ).scalar_one_or_none()

    if user is None:
        if not password:
            raise InvitationError("a password is required to create the account")
        # Raises PasswordPolicyError on a weak password. The endpoint maps it to
        # 422, the transaction rolls back, and the invitation stays live so the
        # person can retry with the same link.
        user = User(
            email=invitation.email,
            hashed_password=hash_password(password),
            full_name=(full_name or None),
            is_active=True,
            is_superuser=False,
        )
        db.add(user)
        await db.flush()
        created = True
    else:
        if password is not None:
            raise InvitationError(
                "This address already has an account; sign in to accept"
            )
        if not user.is_active or user.deleted_at is not None:
            raise InvitationRecipientInvalidError("That account is disabled")
        if await _is_member(
            db, workspace_id=invitation.workspace_id, user_id=user.id
        ):
            # The membership was created after this invitation was issued, most
            # likely by an admin adding them directly. Granting it twice would
            # fail the unique constraint and surface as a 500.
            raise InvitationRecipientInvalidError(
                "That address is already a member of this workspace"
            )
        if full_name and not user.full_name:
            user.full_name = full_name
        created = False

    membership = WorkspaceMembership(
        workspace_id=invitation.workspace_id,
        user_id=user.id,
        # From the row, never from the request.
        role=invitation.role,
    )
    db.add(membership)
    invitation.accepted_by_user_id = user.id
    await db.flush()

    logger.info(
        "invitation_accepted",
        invitation_id=str(invitation.id),
        workspace_id=str(invitation.workspace_id),
        user_id=str(user.id),
        role=str(invitation.role),
        created_user=created,
    )
    return AcceptedInvitation(
        user=user, membership=membership, created_user=created
    )


async def purge_expired(db: AsyncSession, *, older_than_days: int = 30) -> int:
    """Delete invitations closed long ago.

    Accepted and revoked rows are kept for a while: "who invited this person, and
    when" is a question an audit should be able to answer, and deleting the row
    on use would answer it only in the log.
    """
    from sqlalchemy import delete

    cutoff = utcnow() - timedelta(days=older_than_days)
    result = await db.execute(
        delete(WorkspaceInvitation).where(
            WorkspaceInvitation.expires_at < cutoff,
            WorkspaceInvitation.accepted_at.is_not(None)
            | WorkspaceInvitation.revoked_at.is_not(None),
        )
    )
    return int(result.rowcount or 0)
