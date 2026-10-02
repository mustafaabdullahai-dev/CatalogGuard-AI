"""Invitation management and acceptance (D-017).

Two audiences, one router:

  - `POST /workspaces/{id}/invitations` and friends require the `admin`
    capability and act inside a workspace.
  - `POST /auth/invitations/accept` is unauthenticated, because the whole point
    is to onboard someone who has no account yet.

The acceptance endpoint is the only unauthenticated write path in the system, so
it is worth saying plainly what it can do: create a user with a password the
caller supplies, and attach that user to one workspace with a role that came
from the token. It cannot choose the workspace, cannot choose the role, cannot
grant `admin` without an `admin` having issued the invitation, and cannot be
replayed. Everything else about an account still requires a signed-in session.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantScope, require_workspace_admin
from app.core.logging import get_logger
from app.core.security.passwords import PasswordPolicyError
from app.db.session import get_session
from app.models.tenancy import UserRole
from app.schemas.invitations import (
    AcceptInvitationRequest,
    AcceptInvitationResponse,
    InvitationIssuedResponse,
    InvitationListResponse,
    InvitationRef,
    InviteRequest,
)
from app.services.auth import invitations as invite_service

router = APIRouter(tags=["invitations"])
logger = get_logger(__name__)


@router.post(
    "/invitations",
    response_model=InvitationIssuedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite someone to a workspace with a specific role",
)
async def create_invitation(
    body: InviteRequest,
    scope: Annotated[TenantScope, Depends(require_workspace_admin())],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> InvitationIssuedResponse:
    """Issue an invitation, bound to the role chosen here.

    `admin` is required because this endpoint creates a membership with an
    arbitrary role. Letting `write` or `approve` call it would make privilege
    escalation a property of the role matrix, which D-003 and D-004 exist to
    prevent.

    `resend=true` reissues the token on an existing live invitation instead of
    erroring. That is the response to a suspected leak: the old token stops
    working the moment the new one is written.
    """
    try:
        existing = await invite_service.invite(
            db,
            workspace_id=scope.workspace_id,
            email=body.email,
            role=UserRole(body.role),
            invited_by=scope.user,
            resend=False,
        )
    except invite_service.InvitationAlreadySentError:
        # Point at the resend endpoint rather than failing: hitting this twice is
        # the normal consequence of a double-click, not a mistake worth an error.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An invitation is already pending for that address; use the "
            "resend endpoint to reissue its token",
        ) from None
    except invite_service.InvitationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    await db.commit()
    return InvitationIssuedResponse(
        invitation=InvitationRef.from_record(
            existing.record, invited_by=scope.user.id
        ),
        resent=False,
        queued_message_id=existing.queued_message_id,
    )


@router.post(
    "/invitations/resend",
    response_model=InvitationIssuedResponse,
    summary="Reissue a pending invitation",
)
async def resend_invitation(
    body: InviteRequest,
    scope: Annotated[TenantScope, Depends(require_workspace_admin())],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> InvitationIssuedResponse:
    """Issue a fresh token for a pending invitation, killing the old one."""
    try:
        existing = await invite_service.invite(
            db,
            workspace_id=scope.workspace_id,
            email=body.email,
            role=UserRole(body.role),
            invited_by=scope.user,
            resend=True,
        )
    except invite_service.InvitationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    await db.commit()
    logger.info(
        "invitation_resent",
        invitation_id=str(existing.record.id),
        workspace_id=str(scope.workspace_id),
    )
    return InvitationIssuedResponse(
        invitation=InvitationRef.from_record(
            existing.record, invited_by=scope.user.id
        ),
        resent=True,
        queued_message_id=existing.queued_message_id,
    )


@router.get(
    "/invitations",
    response_model=InvitationListResponse,
    summary="List invitations for a workspace",
)
async def list_invitations(
    scope: Annotated[TenantScope, Depends(require_workspace_admin())],
    db: Annotated[AsyncSession, Depends(get_session)],
    include_closed: Annotated[
        bool, Query(description="Include accepted and revoked invitations")
    ] = False,
) -> InvitationListResponse:
    records = await invite_service.list_invitations(
        db, workspace_id=scope.workspace_id, include_closed=include_closed
    )
    return InvitationListResponse(
        invitations=[
            InvitationRef.from_record(r, invited_by=r.invited_by_user_id)
            for r in records
        ]
    )


@router.delete(
    "/invitations/{invitation_id}",
    summary="Revoke a pending invitation",
)
async def revoke_invitation(
    invitation_id: UUID,
    scope: Annotated[TenantScope, Depends(require_workspace_admin())],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    """Revoke an invitation nobody has redeemed.

    Scoped by workspace on the way in as well as in SQL: revoking by id alone
    would let one workspace's admin cancel another's invitation by guessing a
    UUID. A 404 covers both "no such invitation" and "not yours", because the
    difference is itself the answer a prober wants.
    """
    revoked = await invite_service.revoke(
        db, invitation_id=invitation_id, workspace_id=scope.workspace_id
    )
    await db.commit()
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found"
        )
    logger.info(
        "invitation_revoked",
        invitation_id=str(invitation_id),
        workspace_id=str(scope.workspace_id),
        revoked_by=str(scope.user.id),
    )
    return {"revoked": True}


@router.post(
    "/auth/invitations/accept",
    response_model=AcceptInvitationResponse,
    summary="Accept an invitation and join the workspace",
)
async def accept_invitation(
    body: AcceptInvitationRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AcceptInvitationResponse:
    """Redeem a token: create the account if needed, then create the membership.

    Unauthenticated by design. The token is the credential, and it was issued by
    someone who already holds `admin` in the workspace it names.

    A weak password raises before anything commits, so the invitation survives
    and the person can retry with the same link rather than ask for another.
    """
    try:
        result = await invite_service.accept(
            db,
            token=body.token,
            password=body.password,
            full_name=body.full_name,
        )
    except invite_service.InvitationNotFoundError:
        # Expired, revoked, already accepted, and unknown are one message.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This invitation link is invalid or has expired",
        ) from None
    except invite_service.InvitationRecipientInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except invite_service.InvitationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except PasswordPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    await db.commit()
    return AcceptInvitationResponse(
        workspace_id=result.membership.workspace_id,
        role=str(result.membership.role),
        account_created=result.created_user,
    )
