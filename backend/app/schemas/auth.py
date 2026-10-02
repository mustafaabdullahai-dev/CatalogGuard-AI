"""Authentication request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

Password = Annotated[
    str,
    Field(
        min_length=1,
        # Argon2 at the configured cost is deliberately expensive, so the
        # request body is bounded before the hash runs rather than after.
        max_length=1024,
        description="Plaintext password. Never logged, never returned.",
    ),
]


class LoginRequest(BaseModel):
    email: str = Field(max_length=320, description="Case-insensitive address")
    password: Password

    @field_validator("email")
    @classmethod
    def _normalize(cls, v: str) -> str:
        """Lowercase and strip before anything looks the user up.

        Doing it here rather than at each call site means the throttle
        counter, the lookup, and the rate-limit key all agree on one spelling of
        the same address. Case-sensitive matching would also let an attacker
        mint unlimited throttle budgets by varying case.
        """
        value = v.strip().lower()
        if not value or "@" not in value or value.startswith("@"):
            raise ValueError("not a valid email address")
        return value


class ChangePasswordRequest(BaseModel):
    current_password: Password
    new_password: Password


class WorkspaceRef(BaseModel):
    """A workspace the signed-in user may act in."""

    id: UUID
    name: str
    slug: str
    role: str


class SessionUser(BaseModel):
    """The identity returned to the client.

    Deliberately excludes `hashed_password`, and the model is constructed
    explicitly rather than by dumping the ORM row, so a column added to `users`
    later cannot leak by default.
    """

    id: UUID
    email: str
    full_name: str | None
    is_superuser: bool


class LoginResponse(BaseModel):
    user: SessionUser
    workspaces: list[WorkspaceRef]
    # Tells the client the stored hash was upgraded to current cost parameters
    # during this login. Useful in logs, ignored by the UI.
    password_rehashed: bool = False


class CurrentSessionResponse(BaseModel):
    user: SessionUser
    workspaces: list[WorkspaceRef]
    expires_at: datetime


class PasswordResetRequest(BaseModel):
    email: str = Field(max_length=320, description="Case-insensitive address")

    @field_validator("email")
    @classmethod
    def _normalize(cls, v: str) -> str:
        """Normalized here for the same reason as login.

        The throttle key, the lookup, and the message recipient must all agree
        on one spelling, or `Ops@example.com` gets a fresh budget on every
        attempt and bypasses the limit entirely.
        """
        value = v.strip().lower()
        if not value or "@" not in value or value.startswith("@"):
            raise ValueError("not a valid email address")
        return value


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=16, max_length=512)
    new_password: Password


class PasswordResetRequestedResponse(BaseModel):
    """The only response the request endpoint ever returns.

    Identical for a known address, an unknown one, and a disabled account. A
    field like `"sent": true` would make this endpoint an account-existence
    oracle, which is worse than the problem a reset solves: the person using it
    is, by definition, someone who cannot prove who they are.
    """

    accepted: bool = True
    # Advisory only: a client may render this but must not branch on it. The
    # wording says "queued" rather than "sent" because the only transport is the
    # D-017 outbox, and a UI claiming delivery that has not happened is exactly
    # the kind of small lie that makes a support queue undiagnosable.
    message: str = (
        "If that address has an active account, a reset link has been queued "
        "for delivery."
    )


class PasswordResetCompletedResponse(BaseModel):
    """No session is issued here.

    The caller is signing in from an unknown device, and returning a session
    would make the reset the thing that authenticates an attacker.
    """

    password_reset: bool = True
    sessions_revoked: int


class CsrfTokenResponse(BaseModel):
    """A CSRF token for the double-submit header.

    Also set as a cookie. The client reads it from either; this endpoint exists
    so a client does not have to parse `Set-Cookie`.
    """

    csrf_token: str
    header_name: str
