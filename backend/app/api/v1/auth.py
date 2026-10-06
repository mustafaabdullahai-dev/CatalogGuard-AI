"""Sign-in, sign-out, and password change (D-014).

Deliberately no endpoints for creating users or granting roles. A user cannot
self-register and cannot promote themselves: those paths belong to a CLI run by
someone with database access, so an unauthenticated caller has no route to any
identity at all.

Every failure here is one 401 with one message. The reason is recorded
server-side and never returned, because any difference in response — status,
body, or timing — is information an attacker can use to enumerate accounts.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth_context
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.middleware import CSRFTokens
from app.core.security.passwords import PasswordPolicyError
from app.db.session import get_session, get_session_no_commit
from app.models.tenancy import User, Workspace, WorkspaceMembership
from app.schemas.auth import (
    ChangePasswordRequest,
    CsrfTokenResponse,
    CurrentSessionResponse,
    LoginRequest,
    LoginResponse,
    PasswordResetCompletedResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetRequestedResponse,
    SessionUser,
    WorkspaceRef,
)
from app.services.auth import login as login_service
from app.services.auth import password_reset as reset_service
from app.services.auth import sessions as session_service

router = APIRouter(tags=["auth"])
logger = get_logger(__name__)


def _client_ip(request: Request) -> str | None:
    """Peer address, ignoring `X-Forwarded-For`.

    The header is client-controlled and is only trustworthy behind a proxy this
    deployment owns. Treating an unverified header as the source of a rate-limit
    key would let a caller mint a fresh budget per request by inventing a value.
    If a proxy is ever put in front, this is the place that has to change, and
    it should change to a configured, trusted-proxy count rather than to blind
    header parsing.
    """
    client = request.client
    return client.host if client else None


def _session_user(user: User) -> SessionUser:
    """Project the ORM row onto the response model field by field.

    Not `model_validate(user)` and not a dict dump: a column added to `users`
    later would then be exposed by default, and `hashed_password` is exactly the
    kind of column that gets added casually.
    """
    return SessionUser(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_superuser=user.is_superuser,
    )


async def _workspaces_for(
    db: AsyncSession, memberships: tuple[WorkspaceMembership, ...]
) -> list[WorkspaceRef]:
    """Resolve memberships into the workspaces the client can switch between."""
    if not memberships:
        return []
    ids = [m.workspace_id for m in memberships]
    found = await db.execute(select(Workspace).where(Workspace.id.in_(ids)))
    by_id = {w.id: w for w in found.scalars()}
    return [
        WorkspaceRef(
            id=workspace.id,
            name=workspace.name,
            slug=workspace.slug,
            role=str(membership.role),
        )
        for membership in memberships
        if (workspace := by_id.get(membership.workspace_id)) is not None
    ]


def _set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        # HttpOnly: script on the page must not be able to read the session.
        # This is the whole reason the cookie scheme beat a JWT in localStorage.
        httponly=True,
        secure=settings.cookie_secure,
        # Lax rather than Strict: Strict would not send the cookie when a user
        # follows a link into the app from a notification email, which reads as
        # being logged out at random.
        samesite=settings.session_cookie_samesite,
        path="/",
    )


@router.post(
    "/auth/login",
    response_model=LoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Sign in with email and password",
)
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_session_no_commit)],
) -> LoginResponse:
    """Exchange credentials for a session cookie.

    CSRF is enforced by `CSRFMiddleware` on every state-changing API call,
    including this one. Without that check an attacker could sign a victim into
    an attacker-controlled account ("login CSRF") and then read whatever the
    victim does while believing they are signed in as themselves.
    """
    settings = get_settings()
    ip_address = _client_ip(request)
    try:
        result = await login_service.authenticate(
            db,
            email=body.email,
            password=body.password,
            ip_address=ip_address,
            user_agent=request.headers.get("user-agent"),
        )
    except login_service.AuthenticationError:
        # `manual_session` has no automatic commit, so the throttle counters
        # written during the attempt are still pending. Commit them explicitly:
        # a failed login must survive this request, and the raise below would
        # otherwise be the end of the transaction.
        await db.commit()
        logger.warning(
            "login_failed",
            email=body.email,
            ip=ip_address,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Cookie"},
        ) from None

    workspaces = await _workspaces_for(
        db, tuple(await login_service.list_memberships(db, result.user))
    )
    await db.commit()
    _set_session_cookie(response, settings, result.session.token)
    logger.info(
        "login_succeeded",
        user_id=str(result.user.id),
        ip=ip_address,
        workspaces=len(workspaces),
        password_rehashed=result.rehashed,
    )
    return LoginResponse(
        user=_session_user(result.user),
        workspaces=workspaces,
        password_rehashed=result.rehashed,
    )


@router.post("/auth/logout", summary="Revoke the current session")
async def logout(
    response: Response,
    context: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    """Revoke this session only.

    Other devices stay signed in. "Log out everywhere" belongs on password
    change, which is where an unexpected device gets cut off.
    """
    await session_service.revoke_session(context.session)
    await db.commit()
    settings = get_settings()
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        # The attributes must match the ones used when setting it, or the
        # browser keeps the original cookie.
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.session_cookie_samesite,
    )
    return {"revoked": True}


@router.get(
    "/auth/me",
    response_model=CurrentSessionResponse,
    summary="The current session's identity and workspaces",
)
async def me(
    context: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CurrentSessionResponse:
    return CurrentSessionResponse(
        user=_session_user(context.user),
        workspaces=await _workspaces_for(db, context.memberships),
        expires_at=context.session.expires_at,
    )


@router.post("/auth/password", summary="Change your own password")
async def change_password(
    body: ChangePasswordRequest,
    context: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, bool]:
    """Change the password and revoke every other session.

    The current session survives, so the caller is not signed out of the tab
    they changed it in; every other device is revoked immediately. The current
    password is required regardless, because a stolen cookie must not be enough
    to take the account over permanently.
    """
    if body.current_password == body.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ from the current one",
        )
    try:
        await login_service.change_password(
            db,
            user=context.user,
            current_password=body.current_password,
            new_password=body.new_password,
            current_session=context.session,
        )
    except login_service.AuthenticationError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
            headers={"WWW-Authenticate": "Cookie"},
        ) from None
    except PasswordPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    await db.commit()
    logger.info("password_changed", user_id=str(context.user.id))
    return {"password_changed": True}


@router.get(
    "/auth/csrf",
    response_model=CsrfTokenResponse,
    summary="Obtain a CSRF token",
)
async def csrf(request: Request, response: Response) -> CsrfTokenResponse:
    """Hand out a CSRF token and the cookie that must accompany it.

    The middleware already issues a token on any safe request, so a client that
    loads a page is ready to POST. This endpoint exists so a client can fetch a
    usable token without parsing `Set-Cookie`, and so the header name is
    discoverable rather than duplicated in frontend source.
    """
    settings = get_settings()
    tokens = CSRFTokens(settings)
    existing = request.cookies.get(settings.csrf_cookie_name)
    # Reissue only when the cookie is absent or not one we minted. Written as a
    # statement rather than a conditional expression so the type of `token` is
    # narrowed to `str` rather than staying `str | None`.
    token = existing if existing is not None and tokens.valid(existing) else tokens.issue()
    if token != existing:
        response.set_cookie(
            key=settings.csrf_cookie_name,
            value=token,
            # Readable by script on purpose: the browser client has to copy this
            # value into the request header. The session cookie is HttpOnly and
            # never leaves the browser on its own.
            httponly=False,
            secure=settings.cookie_secure,
            samesite=settings.session_cookie_samesite,
            path="/",
        )
    return CsrfTokenResponse(
        csrf_token=token,
        header_name=settings.csrf_header_name,
    )


@router.post(
    "/auth/password-reset",
    response_model=PasswordResetRequestedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request a password reset link",
)
async def request_password_reset(
    request: Request,
    body: PasswordResetRequest,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> PasswordResetRequestedResponse:
    """Always 202 with the same body, whatever the address turns out to be.

    202 rather than 200 because nothing has happened yet: the message is queued,
    not sent. `PasswordResetRequestedResponse` says so in words too, since a
    client that renders "sent" on a 200 is making a claim the server cannot
    support while the only transport is the outbox.
    """
    await reset_service.request_reset(
        db,
        email=body.email,
        ip_address=_client_ip(request),
    )
    await db.commit()
    return PasswordResetRequestedResponse()


@router.post(
    "/auth/password-reset/confirm",
    response_model=PasswordResetCompletedResponse,
    summary="Redeem a reset token",
)
async def confirm_password_reset(
    body: PasswordResetConfirm,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> PasswordResetCompletedResponse:
    """Set the new password and revoke every session the account had.

    Revoking all sessions is the point: a reset is usually somebody's response
    to a compromise, and a session cookie issued to the intruder survives a
    password change unless it is explicitly killed.

    No session is returned. The reset came from a link in an inbox; issuing a
    session here would authenticate whoever presented the link, which is the
    one party this flow cannot yet identify.
    """
    try:
        result = await reset_service.confirm_reset(
            db, token=body.token, new_password=body.new_password
        )
    except reset_service.InvalidResetTokenError:
        # Expired, already used, revoked, and unknown all read the same, so the
        # response cannot be used to learn which guesses were close.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset link is invalid or has expired",
        ) from None
    except PasswordPolicyError as exc:
        # Thrown before anything is committed, so the token stays unconsumed and
        # the person can retry with the same link.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    await db.commit()
    return PasswordResetCompletedResponse(sessions_revoked=result.sessions_revoked)
