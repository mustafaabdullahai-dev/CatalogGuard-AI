"""Where a marketplace name becomes an adapter.

One registry, deliberately small. Registration is keyed by the marketplace
slug stored on `MarketplaceConnection.marketplace`, so the lookup a caller
performs is the same one the database already performed.

The registry holding exactly one entry is the honest state of D-005: no
Shopify, Amazon or TikTok Shop adapter exists, and inventing names for them
would imply otherwise. When one is added it registers here and nowhere else —
nothing in `app/` outside this package is allowed to branch on a marketplace.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from app.connectors.base import MarketplaceConnector, UnknownMarketplaceError
from app.connectors.mock import MockConnector

ConnectionFactory = Callable[[Mapping[str, str]], MarketplaceConnector]


def _mock(credentials: Mapping[str, str]) -> MarketplaceConnector:
    return MockConnector(credentials=credentials)


CONNECTOR_FACTORIES: dict[str, ConnectionFactory] = {"mock": _mock}


def connector_for(marketplace: str, credentials: Mapping[str, str]) -> MarketplaceConnector:
    """Build the adapter for `marketplace` with already-decrypted credentials.

    Raises `UnknownMarketplaceError` rather than falling back to the mock: a
    call that expected Shopify and silently got an in-memory dict would report
    success against a store that does not exist.
    """
    factory = CONNECTOR_FACTORIES.get(marketplace)
    if factory is None:
        known = ", ".join(sorted(CONNECTOR_FACTORIES)) or "none"
        raise UnknownMarketplaceError(
            f"no connector registered for {marketplace!r} (registered: {known}). "
            "No live marketplace adapter exists yet: D-005 defers Shopify, "
            "Amazon and TikTok Shop until the core model is stable."
        )
    return factory(credentials)
