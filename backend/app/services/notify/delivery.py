"""Outbound mail delivery (D-017).

The port, and one adapter.

Why a port at all
    Password reset and invitation both reduce to "hand this person a link". The
    link logic is the security-critical half and it is fully testable without a
    network. The send is the half that needs a provider credential nobody has
    supplied yet. Splitting them means the untestable part is one small class
    instead of every path that hands out account access.

Why `outbox` writes a row and claims nothing
    The adapter records the message and leaves `sent_at` NULL. A row that says
    it was never sent is a queue someone can drain once a provider exists; a
    row marked sent that was never sent is a support ticket about a reset link
    that will never arrive. The distinction is the whole point of the outbox.

Why the link is never logged, in any environment
    A reset link in a log file is a live credential in whatever aggregates logs.
    It would also be useless: the D-010 masker redacts any run of 32 or more
    characters, which is longer than every token this system issues. So a
    development-only "log the link anyway" branch would produce a line that
    looks like it contains a usable link and does not, which is worse than no
    branch at all. Retrieving a queued message means reading `email_outbox`, or
    running `python -m app.cli.outbox`, which prints them without going near a
    log pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.tenancy import EmailOutbox

logger = get_logger(__name__)

TEMPLATE_RESET = "password_reset"
TEMPLATE_INVITATION = "workspace_invitation"


@dataclass(frozen=True, slots=True)
class MailMessage:
    """One outbound message.

    Plaintext body rather than a template reference plus context: the two
    templates are short, and rendering at send time means a template change
    silently rewrites the body of a message that was already queued. The queued
    text is what the recipient will get, so it is the text that is stored.
    """

    recipient: str
    subject: str
    body_text: str
    template: str
    related_id: UUID | None = None


@runtime_checkable
class MailDelivery(Protocol):
    """Hands a message to a transport. Implementations must not raise on failure.

    A delivery failure is recorded, not raised: the caller is about to commit a
    transaction containing a live token, and a failed send should leave that
    token usable rather than unwound. The operator retries from the outbox.
    """

    channel: str

    async def deliver(self, db: AsyncSession, message: MailMessage) -> UUID:
        """Persist or send `message`; return a reference id for the audit log."""
        ...


class OutboxDelivery:
    """Default adapter: record the message and stop.

    Named for what it does rather than what it simulates. It does not pretend to
    send, and it does not retry.
    """

    channel = "outbox"

    async def deliver(self, db: AsyncSession, message: MailMessage) -> UUID:
        record = EmailOutbox(
            recipient=message.recipient,
            subject=message.subject,
            body_text=message.body_text,
            template=message.template,
            related_id=message.related_id,
            delivery_channel=self.channel,
            sent_at=None,
        )
        db.add(record)
        await db.flush()

        # Recipient and template answer the question an operator actually asks
        # ("did the system try to send them anything"), and nothing here can be
        # used to take the account over.
        logger.info(
            "email_queued",
            outbox_id=str(record.id),
            template=message.template,
            recipient=message.recipient,
            sent=record.sent_at is not None,
            delivery_channel=record.delivery_channel,
        )
        return record.id


def get_delivery() -> MailDelivery:
    """The configured transport.

    One place to change when a provider arrives. Raising on an unknown value
    rather than silently falling back to the outbox: a deployment that believes
    it is sending mail and is not is the failure this port exists to prevent.
    """
    settings = get_settings()
    if settings.mail_delivery == "outbox":
        return OutboxDelivery()
    raise ValueError(f"unsupported MAIL_DELIVERY: {settings.mail_delivery}")


def reset_link(token: str) -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/reset-password?token={token}"


def invitation_link(token: str) -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/accept-invitation?token={token}"


def password_reset_message(*, recipient: str, token: str) -> MailMessage:
    ttl_minutes = get_settings().reset_token_ttl_minutes
    return MailMessage(
        recipient=recipient,
        subject="Reset your CatalogGuard password",
        template=TEMPLATE_RESET,
        related_id=None,
        body_text=(
            f"A password reset was requested for this address.\n\n"
            f"{reset_link(token)}\n\n"
            f"The link is valid for {ttl_minutes} minutes and can be used once. "
            f"If you did not request this, no action is needed: nothing has "
            f"changed on your account, and if someone else requested it they "
            f"cannot use the link without this inbox.\n"
        ),
    )


def invitation_message(
    *, recipient: str, workspace_name: str, role: str, invited_by: str | None, token: str
) -> MailMessage:
    ttl_hours = get_settings().invitation_token_ttl_hours
    inviter = invited_by or "An administrator"
    return MailMessage(
        recipient=recipient,
        subject=f"You have been invited to {workspace_name} on CatalogGuard",
        template=TEMPLATE_INVITATION,
        related_id=None,
        body_text=(
            f"{inviter} invited you to the workspace \"{workspace_name}\" "
            f"with the role {role}.\n\n"
            f"{invitation_link(token)}\n\n"
            f"The link is valid for {ttl_hours} hours and can be used once.\n"
        ),
    )
