"""Stored marketplace connections (Phase 19, §33).

A `MarketplaceConnection` row holds ciphertext and nothing else, so the three
questions this file answers are the ones the seam needs from the database:
how a credential set gets in, how it comes back out, and how a stored row
becomes an adapter to call.

The first writer matters for the same reason the first writer of `audit_logs`
did: until something inserts a row, "credentials are encrypted at rest" is a
column comment. `create_connection` is the only path that writes one, and it
never passes plaintext anywhere but the encryptor.

No route calls these yet. HTTP for connections belongs with the UI that needs
it; keeping it out here means nothing exposes a credential-handling endpoint
before there is a reason to.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import (
    connector_for,
    decrypt_credentials,
    encrypt_credentials,
)
from app.connectors.base import MarketplaceConnector
from app.db.base import TenantContext
from app.db.repository import TenantRepository
from app.models.platform import ConnectionStatus, MarketplaceConnection


class ConnectionRepository(TenantRepository[MarketplaceConnection]):
    model = MarketplaceConnection


async def create_connection(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    marketplace: str,
    display_name: str,
    credentials: dict[str, str],
    granted_scopes: Sequence[str] = (),
    token_expires_at: datetime | None = None,
) -> MarketplaceConnection:
    """Store a credential set, encrypted, in `PENDING` status.

    `PENDING` rather than `ACTIVE`: nothing has been told the marketplace yet,
    and a row that claimed otherwise would be a status nobody verified.
    """
    connection = MarketplaceConnection(
        marketplace=marketplace,
        display_name=display_name,
        status=ConnectionStatus.PENDING,
        encrypted_credentials=encrypt_credentials(credentials),
        granted_scopes=list(granted_scopes),
        token_expires_at=token_expires_at,
    )
    await ConnectionRepository(db, tenant).add(connection)
    return connection


def credentials_for(connection: MarketplaceConnection) -> dict[str, str]:
    """Decrypt a stored connection's credential set."""
    return decrypt_credentials(connection.encrypted_credentials)


def connector_for_connection(connection: MarketplaceConnection) -> MarketplaceConnector:
    """Build the adapter for a stored connection.

    The decrypt-and-dispatch step Phase 20 will call: one place that reads a
    secret, so no execution path can end up handling one by accident.
    """
    return connector_for(connection.marketplace, credentials_for(connection))
