"""The marketplace seam (Phase 19, §22, D-005, §33).

Why there is an abstraction before there is a marketplace
    D-005 deferred every live integration: Shopify, Amazon and TikTok Shop are
    post-MVP, because building connectors against an internal model that had
    not settled once meant rework for nothing. What D-005 did *not* defer is
    the seam — one interface, one adapter that never leaves the process — so
    that Phase 20's execution engine has something to call and the core learns
    now that marketplace knowledge lives on one side of a boundary. Nine
    operations, one registry, no marketplace named anywhere else in the app.

Why the errors are part of the interface
    §21's execution path must preserve an error, retry a bounded number of
    times, and say what recovery looks like. Those are decisions about the
    *class* of failure — a rate limit is worth retrying, rejected credentials
    and an unknown product are not — so `retryable` is declared on the error
    rather than inferred by each caller from a message string. A connector
    that invents its own exception types hands that decision back to everyone
    downstream.

Why rollback is an operation and a capability flag together
    §21 says to implement rollback where the API supports it and to never
    pretend otherwise. So the interface carries `rollback` *and*
    `supports_rollback`: an adapter that does not support it must refuse
    loudly, and a caller that believes it does can ask. `ExecutionJob`
    already stores `previous_state` and `rollback_supported` for exactly this.

Why nothing here performs I/O
    The mock keeps state in a dict. There is no HTTP client in this package,
    no rate limiter, no OAuth dance — those belong to adapters that D-005
    keeps out of the MVP, and writing them against a marketplace nobody has
    authorized would be code that can only ever be guessed at.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class ConnectorError(Exception):
    """Something the marketplace side refused or failed to do.

    `retryable` is the contract §21's bounded-retry loop reads: `True` means a
    later attempt may succeed on its own (a limit, a timeout), `False` means
    the same request will fail the same way again and a human must change
    something first.
    """

    retryable: bool = False

    def __init__(self, message: str, *, marketplace: str | None = None) -> None:
        super().__init__(message)
        self.marketplace = marketplace


class ConnectorAuthError(ConnectorError):
    """Credentials rejected, expired or revoked. Never worth retrying as-is."""

    retryable = False


class ConnectorNotFoundError(ConnectorError):
    """The id does not exist on that marketplace."""

    retryable = False


class ConnectorRateLimitError(ConnectorError):
    """The marketplace asked us to slow down. Retryable, with a delay."""

    retryable = True


class ConnectorTransientError(ConnectorError):
    """A network or server-side failure that may not repeat."""

    retryable = True


class ConnectorUnsupportedError(ConnectorError):
    """This marketplace cannot do that operation, and we will not fake it."""

    retryable = False


class UnknownMarketplaceError(ValueError):
    """No adapter is registered for that marketplace name."""


class ListingState(StrEnum):
    """The vocabulary an adapter maps marketplace-specific states into.

    Marketplaces name these differently (`active`/`paused`/`draft`,
    `LIVE`/`SUPPRESSED`). Mapping happens in the adapter, which is the whole
    point of having one: the core compares values it can actually compare.
    """

    ACTIVE = "active"
    PAUSED = "paused"
    DRAFT = "draft"
    ARCHIVED = "archived"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class MarketplaceProduct:
    """A product as the marketplace reports it.

    `attributes` keeps the marketplace's own field names on purpose. Renaming
    them into ours is catalog work, not connector work; the adapter hands back
    what the marketplace said, and the core decides what it means.
    """

    product_id: str
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MarketplaceVariant:
    variant_id: str
    sku: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InventoryLevel:
    """One stock figure. Negative quantities are reported, not corrected."""

    sku: str
    available: int


@dataclass(frozen=True, slots=True)
class ListingStatus:
    product_id: str
    state: ListingState
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ListingValidation:
    """What an adapter found wrong with a listing, in its own words."""

    product_id: str
    ok: bool
    issues: tuple[str, ...] = ()


@runtime_checkable
class MarketplaceConnector(Protocol):
    """Nine operations (§22), synchronous in shape and async in fact.

    Async because every real adapter is an HTTP call, and deciding later that
    the interface was blocking would mean rewriting every caller. The mock
    awaits nothing, which is the correct cost of having no network.
    """

    marketplace: str
    supports_rollback: bool

    async def get_product(self, product_id: str) -> MarketplaceProduct:
        """Fetch one product, or raise `ConnectorNotFoundError`."""
        ...

    async def get_products(
        self, product_ids: Sequence[str]
    ) -> list[MarketplaceProduct]:
        """Fetch several, in input order.

        Strict: an unknown id anywhere in the batch raises rather than being
        skipped. A silently dropped product is work that never happens and
        nothing left behind to say so.
        """
        ...

    async def create_product(self, attributes: Mapping[str, Any]) -> MarketplaceProduct:
        """Create a product and return what the marketplace stored."""
        ...

    async def update_product(
        self, product_id: str, fields: Mapping[str, Any]
    ) -> MarketplaceProduct:
        """Merge `fields` into the product; return the resulting state."""
        ...

    async def delete_product(self, product_id: str) -> None:
        """Delete, or raise `ConnectorNotFoundError`."""
        ...

    async def get_variants(self, product_id: str) -> list[MarketplaceVariant]:
        ...

    async def get_inventory(self, product_id: str) -> list[InventoryLevel]:
        ...

    async def get_listing_status(self, product_id: str) -> ListingStatus:
        ...

    async def validate_listing(self, product_id: str) -> ListingValidation:
        """Report listing problems without changing anything."""
        ...

    async def rollback(
        self, product_id: str, previous_state: Mapping[str, Any]
    ) -> MarketplaceProduct:
        """Restore `previous_state`.

        Implementations that cannot must raise `ConnectorUnsupportedError`
        (§21: do not pretend rollback is possible). The capability flag says
        the same thing before the call.
        """
        ...
