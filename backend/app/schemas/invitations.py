"""Invitation request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.auth import Password


class InviteRequest(BaseModel):
    """An administrator asking to invite someone.

    `role` is validated against `UserRole` by the endpoint, not here: the
    accepted values are the capability matrix, and the matrix is a model, not a
    schema. A duplicated list of role strings in a Pydantic model is a list that
    will drift from the roles that actually grant authority.
    """

    email: str = Field(max_length=320)
    role: str = Field(max_length=64)

    @field_validator("email")
    @classmethod
    def _normalize(cls, v: str) -> str:
        value = v.strip().lower()
        if not value or "@" not in value or value.startswith("@"):
            raise ValueError("not a valid email address")
        return value

    @field_validator("role")
    @classmethod
    def _known_role(cls, v: str) -> str:
        # Rejects a typo at the boundary rather than writing an invitation whose
        # role grants nothing, which would look successful and do nothing.
        from app.models.tenancy import UserRole

        value = v.strip().lower()
        try:
            UserRole(value)
        except ValueError as exc:
            allowed = ", ".join(sorted(str(r) for r in UserRole))
            raise ValueError(f"role must be one of: {allowed}") from exc
        return value


class InvitationRef(BaseModel):
    """An invitation as the admin UI sees it.

    Never carries `token_hash`. The plaintext token exists only inside the
    request that mails it; there is nothing here that could leak it later.
    """

    id: UUID
    email: str
    role: str
    status: str
    invited_by: UUID
    expires_at: datetime
    created_at: datetime

    @classmethod
    def from_record(cls, record, *, invited_by: UUID) -> InvitationRef:
        if record.revoked_at is not None:
            status = "revoked"
        elif record.accepted_at is not None:
            status = "accepted"
        else:
            status = "pending"
        return cls(
            id=record.id,
            email=record.email,
            role=str(record.role),
            status=status,
            invited_by=invited_by,
            expires_at=record.expires_at,
            created_at=record.created_at,
        )


class InvitationListResponse(BaseModel):
    invitations: list[InvitationRef]


class InvitationIssuedResponse(BaseModel):
    """Confirmation that an invitation went out.

    No token, no link. With the outbox adapter the message is a row, not an
    email, so returning a link here would imply delivery that has not happened.
    """

    invitation: InvitationRef
    resent: bool
    queued_message_id: UUID


class AcceptInvitationRequest(BaseModel):
    token: str = Field(min_length=16, max_length=512)
    # Required only when the address has no account yet. Supplying one for an
    # existing account is rejected rather than applied, so an invitation link
    # cannot be used to overwrite a credential somebody else chose.
    password: Password | None = None
    full_name: Annotated[str, Field(max_length=255)] | None = None


class AcceptInvitationResponse(BaseModel):
    """No session is issued. The new user signs in like anyone else."""

    workspace_id: UUID
    role: str
    account_created: bool
