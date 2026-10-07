"""The mock adapter: the seam, exercised, with no network in it (D-005).

What it is for
    D-005 requires that the `MarketplaceConnector` seam be *proven* before any
    live integration exists. Proof means something calls all nine operations,
    gets typed answers, and finds the same failure vocabulary a real adapter
    would raise — which is what lets Phase 20's execution engine be written
    against this package and still be correct against a marketplace later.

What it deliberately does not simulate
    No latency, no auth dance, no rate-limit window, no pagination. Inventing
    those without an API to copy would be a guess dressed up as coverage, and
    the tests that would "prove" rate limiting would be proving our own
    arithmetic. `fail_once` exists instead: it lets a caller demonstrate that
    its retry loop stops, which is the behaviour worth checking.

How credentials are handled
    The adapter reads `api_key` from the decrypted credentials and refuses to
    operate without it, so the "did anybody actually authenticate" branch of
    the code path is exercised too. It keeps the dict it was given, never
    copies it anywhere it could be logged, and its repr does not include it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.connectors.base import (
    ConnectorAuthError,
    ConnectorError,
    ConnectorNotFoundError,
    ConnectorUnsupportedError,
    InventoryLevel,
    ListingState,
    ListingStatus,
    ListingValidation,
    MarketplaceProduct,
    MarketplaceVariant,
)


class MockConnector:
    """In-memory implementation of the full interface.

    State is a dict of dicts keyed by product id. Every operation records its
    name in `calls`, so a caller can assert it went through the seam rather
    than reaching around it.
    """

    marketplace = "mock"

    def __init__(
        self,
        *,
        products: Mapping[str, Mapping[str, Any]] | None = None,
        credentials: Mapping[str, str] | None = None,
        supports_rollback: bool = True,
        fail_once: Mapping[str, ConnectorError] | None = None,
    ) -> None:
        self.supports_rollback = supports_rollback
        self.calls: list[str] = []
        self._products: dict[str, dict[str, Any]] = {
            product_id: dict(attributes) for product_id, attributes in (products or {}).items()
        }
        self._credentials = dict(credentials or {})
        self._fail_once = dict(fail_once or {})
        self._sequence = len(self._products)

    # -- seam plumbing ---------------------------------------------------

    def _authorize(self, operation: str) -> None:
        """Auth, then observation, then the staged failure — in that order.

        Auth first because an unauthenticated call must not look like a
        successful one in `calls`. The staged failure last so `fail_once`
        raises after the call has been recorded: the caller did attempt it.
        """
        if not str(self._credentials.get("api_key", "")).strip():
            raise ConnectorAuthError(
                "mock credentials are missing or rejected", marketplace=self.marketplace
            )
        self.calls.append(operation)
        staged = self._fail_once.pop(operation, None)
        if staged is not None:
            raise staged

    def _require(self, product_id: str) -> dict[str, Any]:
        attributes = self._products.get(product_id)
        if attributes is None:
            raise ConnectorNotFoundError(
                f"product {product_id!r} does not exist", marketplace=self.marketplace
            )
        return attributes

    def _state(self, product_id: str) -> ListingState:
        raw = self._require(product_id).get("listing_state", ListingState.ACTIVE)
        try:
            return ListingState(raw)
        except ValueError as exc:
            # Bad seed data is a bug in the caller's fixture, not marketplace
            # behaviour. Loudly, so it never reads as a real listing state.
            raise ValueError(
                f"{product_id!r} has unknown listing_state {raw!r}"
            ) from exc

    @staticmethod
    def _product(product_id: str, attributes: Mapping[str, Any]) -> MarketplaceProduct:
        return MarketplaceProduct(product_id=product_id, attributes=dict(attributes))

    # -- §22 operations --------------------------------------------------

    async def get_product(self, product_id: str) -> MarketplaceProduct:
        self._authorize("get_product")
        return self._product(product_id, self._require(product_id))

    async def get_products(
        self, product_ids: Sequence[str]
    ) -> list[MarketplaceProduct]:
        self._authorize("get_products")
        return [self._product(pid, self._require(pid)) for pid in product_ids]

    async def create_product(self, attributes: Mapping[str, Any]) -> MarketplaceProduct:
        self._authorize("create_product")
        self._sequence += 1
        product_id = f"mock-product-{self._sequence}"
        self._products[product_id] = dict(attributes)
        return self._product(product_id, self._products[product_id])

    async def update_product(
        self, product_id: str, fields: Mapping[str, Any]
    ) -> MarketplaceProduct:
        self._authorize("update_product")
        attributes = self._require(product_id)
        attributes.update(fields)
        return self._product(product_id, attributes)

    async def delete_product(self, product_id: str) -> None:
        self._authorize("delete_product")
        self._require(product_id)
        del self._products[product_id]

    async def get_variants(self, product_id: str) -> list[MarketplaceVariant]:
        self._authorize("get_variants")
        attributes = self._require(product_id)
        variants: list[MarketplaceVariant] = []
        for index, raw in enumerate(attributes.get("variants", []), start=1):
            source = dict(raw)
            variant_id = str(source.pop("variant_id", f"{product_id}-variant-{index}"))
            sku = source.pop("sku", None)
            variants.append(
                MarketplaceVariant(
                    variant_id=variant_id,
                    sku=None if sku is None else str(sku),
                    attributes=source,
                )
            )
        return variants

    async def get_inventory(self, product_id: str) -> list[InventoryLevel]:
        self._authorize("get_inventory")
        attributes = self._require(product_id)
        inventory = attributes.get("inventory", {})
        if isinstance(inventory, Mapping):
            levels = [
                InventoryLevel(sku=str(sku), available=int(quantity))
                for sku, quantity in inventory.items()
            ]
            return sorted(levels, key=lambda level: level.sku)
        raise ValueError(
            f"{product_id!r} has inventory of type {type(inventory).__name__}; "
            "the mock expects a mapping of sku to quantity"
        )

    async def get_listing_status(self, product_id: str) -> ListingStatus:
        self._authorize("get_listing_status")
        state = self._state(product_id)
        reason = self._require(product_id).get("listing_reason")
        return ListingStatus(
            product_id=product_id,
            state=state,
            reason=None if reason is None else str(reason),
        )

    async def validate_listing(self, product_id: str) -> ListingValidation:
        self._authorize("validate_listing")
        attributes = self._require(product_id)
        issues: list[str] = []

        title = attributes.get("title")
        if not isinstance(title, str) or not title.strip():
            issues.append("title is missing")

        price = attributes.get("price")
        if price is None:
            issues.append("price is missing")
        elif isinstance(price, (int, float)) and price < 0:
            issues.append("price is negative")

        inventory = attributes.get("inventory", {})
        if isinstance(inventory, Mapping):
            for sku, quantity in sorted(inventory.items()):
                if int(quantity) < 0:
                    issues.append(f"inventory for {sku} is negative")

        return ListingValidation(
            product_id=product_id,
            ok=not issues,
            issues=tuple(issues),
        )

    async def rollback(
        self, product_id: str, previous_state: Mapping[str, Any]
    ) -> MarketplaceProduct:
        self._authorize("rollback")
        if not self.supports_rollback:
            raise ConnectorUnsupportedError(
                "rollback is not supported by mock", marketplace=self.marketplace
            )
        attributes = self._require(product_id)
        attributes.clear()
        attributes.update(dict(previous_state))
        return self._product(product_id, attributes)

    def __repr__(self) -> str:
        # Credentials are never part of a repr: a connector that appears in a
        # traceback or a log line must not carry the key that opens a store.
        return (
            f"MockConnector(marketplace={self.marketplace!r}, "
            f"products={len(self._products)}, calls={len(self.calls)})"
        )
