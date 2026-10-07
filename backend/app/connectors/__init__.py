"""Marketplace connectors (Phase 19, §22).

The boundary the core talks to: one interface, one registry, one adapter that
never performs I/O. Everything a marketplace knows about itself lives behind
`MarketplaceConnector`; see `app/connectors/base.py` for why.
"""

from __future__ import annotations

from app.connectors.base import (
    ConnectorAuthError,
    ConnectorError,
    ConnectorNotFoundError,
    ConnectorRateLimitError,
    ConnectorTransientError,
    ConnectorUnsupportedError,
    InventoryLevel,
    ListingState,
    ListingStatus,
    ListingValidation,
    MarketplaceConnector,
    MarketplaceProduct,
    MarketplaceVariant,
    UnknownMarketplaceError,
)
from app.connectors.credentials import (
    CredentialError,
    decrypt_credentials,
    encrypt_credentials,
)
from app.connectors.mock import MockConnector
from app.connectors.registry import CONNECTOR_FACTORIES, connector_for

__all__ = [
    "CONNECTOR_FACTORIES",
    "ConnectorAuthError",
    "ConnectorError",
    "ConnectorNotFoundError",
    "ConnectorRateLimitError",
    "ConnectorTransientError",
    "ConnectorUnsupportedError",
    "CredentialError",
    "InventoryLevel",
    "ListingState",
    "ListingStatus",
    "ListingValidation",
    "MarketplaceConnector",
    "MarketplaceProduct",
    "MarketplaceVariant",
    "MockConnector",
    "UnknownMarketplaceError",
    "connector_for",
    "decrypt_credentials",
    "encrypt_credentials",
]
