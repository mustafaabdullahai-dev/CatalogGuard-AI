"""Marketplace connector seam (Phase 19, §22, §33, D-005).

Three claims, each the kind that rots silently if nobody asserts it.

The first is conformance: §22 lists nine operations, and an interface that
grew past eight of them would break Phase 20's execution engine at runtime,
not at import time. The second is that the core names no marketplace — D-005
keeps Shopify, Amazon and TikTok Shop out of the MVP, and a stray module named
after one of them is exactly how that deferral gets quietly undone.

The third is §33: "Marketplace credentials must never be stored as
plaintext." A column comment cannot enforce that, so these tests read the
table's columns, encrypt through the only writer, and check that the secret
is gone from what would reach the database.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import inspect

from app import connectors
from app.connectors import (
    CONNECTOR_FACTORIES,
    ConnectorAuthError,
    ConnectorNotFoundError,
    ConnectorRateLimitError,
    ConnectorTransientError,
    ConnectorUnsupportedError,
    CredentialError,
    MarketplaceConnector,
    MockConnector,
    UnknownMarketplaceError,
    connector_for,
    decrypt_credentials,
    encrypt_credentials,
)
from app.db.base import TenantContext
from app.models.platform import ConnectionStatus, MarketplaceConnection
from app.services.connections import (
    connector_for_connection,
    create_connection,
    credentials_for,
)

CREDENTIALS = {"api_key": "mock-key-0001", "shop_domain": "example.test"}

SECTION_22_OPERATIONS = (
    "get_product",
    "get_products",
    "update_product",
    "create_product",
    "delete_product",
    "get_variants",
    "get_inventory",
    "get_listing_status",
    "validate_listing",
)

PRODUCTS: dict[str, dict[str, Any]] = {
    "p-1": {
        "title": "Blue mug",
        "price": 12,
        "listing_state": "active",
        "inventory": {"SKU-1": 4, "SKU-2": 0},
        "variants": [
            {"variant_id": "v-1", "sku": "SKU-1", "colour": "blue"},
            {"sku": "SKU-2", "colour": "navy"},
        ],
    }
}


@pytest.fixture
def connector() -> MockConnector:
    return MockConnector(products=PRODUCTS, credentials=CREDENTIALS)


class TestSection22Conformance:
    def test_every_operation_master_prompt_lists_exists(self) -> None:
        for name in SECTION_22_OPERATIONS:
            assert hasattr(MarketplaceConnector, name), name
            assert hasattr(MockConnector, name), name

    def test_rollback_is_on_the_interface_too(self) -> None:
        """§21 needs it; a seam without it would force callers around it."""
        assert hasattr(MarketplaceConnector, "rollback")
        assert hasattr(MockConnector, "rollback")

    def test_the_mock_satisfies_the_interface(self) -> None:
        instance = MockConnector(credentials=CREDENTIALS)
        assert isinstance(instance, MarketplaceConnector)

    def test_the_core_never_names_a_live_marketplace(self) -> None:
        """D-005's deferral, checked as a property of the tree.

        The registry may only contain what has actually been built, and no
        module in `app/` may be named after a marketplace that D-005 defers.
        Docstrings may mention them — that is the decision being quoted.
        """
        assert set(CONNECTOR_FACTORIES) == {"mock"}

        package = Path(connectors.__file__).parent
        filenames = " ".join(p.name for p in package.rglob("*.py"))
        for deferred in ("shopify", "amazon", "tiktok"):
            assert deferred not in filenames, deferred


class TestMockAdapterOperations:
    async def test_get_product_returns_what_the_marketplace_has(
        self, connector: MockConnector
    ) -> None:
        product = await connector.get_product("p-1")
        assert product.product_id == "p-1"
        assert product.attributes["title"] == "Blue mug"
        assert connector.calls == ["get_product"]

    async def test_get_products_is_strict_about_unknown_ids(
        self, connector: MockConnector
    ) -> None:
        """A dropped id would be a change that never executes, silently."""
        found = await connector.get_products(["p-1"])
        assert [p.product_id for p in found] == ["p-1"]

        with pytest.raises(ConnectorNotFoundError):
            await connector.get_products(["p-1", "missing"])

    async def test_update_merges_and_reports_the_result(
        self, connector: MockConnector
    ) -> None:
        updated = await connector.update_product("p-1", {"title": "Navy mug"})
        assert updated.attributes["title"] == "Navy mug"
        assert updated.attributes["price"] == 12, "unmentioned fields survive"

    async def test_create_then_get_then_delete(
        self, connector: MockConnector
    ) -> None:
        created = await connector.create_product({"title": "New"})
        assert created.product_id.startswith("mock-product-")
        assert (await connector.get_product(created.product_id)).attributes == {
            "title": "New"
        }

        second = await connector.create_product({"title": "Second"})
        assert second.product_id != created.product_id

        await connector.delete_product(created.product_id)
        with pytest.raises(ConnectorNotFoundError):
            await connector.get_product(created.product_id)

    async def test_unknown_id_is_not_found(self, connector: MockConnector) -> None:
        with pytest.raises(ConnectorNotFoundError):
            await connector.get_product("nope")

    async def test_variants_inventory_listing_and_validation(
        self, connector: MockConnector
    ) -> None:
        variants = await connector.get_variants("p-1")
        assert [v.variant_id for v in variants] == ["v-1", "p-1-variant-2"]
        assert variants[0].attributes == {"colour": "blue"}, "extra fields kept"
        assert variants[1].sku == "SKU-2", "an unlisted id is invented, not lost"

        inventory = await connector.get_inventory("p-1")
        assert [(level.sku, level.available) for level in inventory] == [
            ("SKU-1", 4),
            ("SKU-2", 0),
        ]

        status = await connector.get_listing_status("p-1")
        assert status.state.value == "active"
        assert status.reason is None

        validation = await connector.validate_listing("p-1")
        assert validation.ok is True
        assert validation.issues == ()

    async def test_validation_lists_every_missing_field(
        self, connector: MockConnector
    ) -> None:
        empty = await connector.create_product({"inventory": {"BAD": -1}})
        validation = await connector.validate_listing(empty.product_id)
        assert validation.ok is False
        assert validation.issues == (
            "title is missing",
            "price is missing",
            "inventory for BAD is negative",
        )


class TestAuthenticationIsPartOfTheSeam:
    async def test_no_api_key_means_no_operation(self) -> None:
        for credentials in (None, {}, {"api_key": ""}, {"api_key": "   "}):
            mock = MockConnector(products=PRODUCTS, credentials=credentials)
            with pytest.raises(ConnectorAuthError):
                await mock.get_product("p-1")
            assert mock.calls == [], "a refused call must not look attempted"

    async def test_auth_error_is_not_retryable(self) -> None:
        mock = MockConnector(credentials={})
        with pytest.raises(ConnectorAuthError) as caught:
            await mock.get_product("p-1")
        assert caught.value.retryable is False
        assert caught.value.marketplace == "mock"


class TestRetryableIsAClassOfFailure:
    def test_the_vocabulary_says_which_failures_may_repeat(self) -> None:
        assert ConnectorRateLimitError("later").retryable is True
        assert ConnectorTransientError("maybe").retryable is True
        assert ConnectorNotFoundError("gone").retryable is False
        assert ConnectorUnsupportedError("no").retryable is False
        assert ConnectorAuthError("bad key").retryable is False

    async def test_a_staged_failure_raises_once_then_works(self) -> None:
        """What §21's bounded retry needs: the second attempt can succeed."""
        mock = MockConnector(
            products=PRODUCTS,
            credentials=CREDENTIALS,
            fail_once={"update_product": ConnectorRateLimitError("slow down")},
        )
        with pytest.raises(ConnectorRateLimitError):
            await mock.update_product("p-1", {"title": "x"})

        updated = await mock.update_product("p-1", {"title": "y"})
        assert updated.attributes["title"] == "y"
        assert mock.calls.count("update_product") == 2


class TestRegistry:
    def test_the_only_registered_adapter_is_the_mock(self) -> None:
        built = connector_for("mock", CREDENTIALS)
        assert isinstance(built, MockConnector)
        assert built.marketplace == "mock"

    def test_an_unknown_marketplace_refuses_rather_than_guessing(self) -> None:
        """Falling back to the mock would report writes against a phantom."""
        with pytest.raises(UnknownMarketplaceError) as caught:
            connector_for("shopify", CREDENTIALS)
        message = str(caught.value)
        assert "mock" in message
        assert "D-005" in message

    def test_the_builder_does_not_log_or_keep_the_secret(self) -> None:
        built = connector_for("mock", CREDENTIALS)
        assert "mock-key-0001" not in repr(built)


class TestCredentialsAtRest:
    def test_round_trip_preserves_the_set(self) -> None:
        blob = encrypt_credentials(CREDENTIALS)
        assert decrypt_credentials(blob) == CREDENTIALS

    def test_ciphertext_does_not_contain_the_secret(self) -> None:
        blob = encrypt_credentials(CREDENTIALS)
        for value in CREDENTIALS.values():
            assert value not in blob
        for key in CREDENTIALS:
            assert key not in blob

    def test_key_order_does_not_change_the_plaintext(self) -> None:
        forwards = decrypt_credentials(encrypt_credentials({"a": "1", "b": "2"}))
        backwards = decrypt_credentials(encrypt_credentials({"b": "2", "a": "1"}))
        assert forwards == backwards

    def test_a_tampered_blob_is_a_credential_error(self) -> None:
        blob = encrypt_credentials(CREDENTIALS)
        damaged = blob[:-4] + ("AAAA" if not blob.endswith("AAAA") else "BBBB")
        with pytest.raises(CredentialError):
            decrypt_credentials(damaged)

    def test_a_different_key_cannot_read_it(self) -> None:
        """Key rotation must fail loudly, not decode to something plausible."""
        from cryptography.fernet import Fernet

        blob = encrypt_credentials(CREDENTIALS)
        other = MagicMock()
        other.encryption_key = Fernet.generate_key().decode()
        with (
            patch("app.connectors.credentials.get_settings", return_value=other),
            pytest.raises(CredentialError, match="ENCRYPTION_KEY"),
        ):
            decrypt_credentials(blob)

    @pytest.mark.parametrize("bad", [{}, {"api_key": 123}, {"": "x"}])
    def test_underspecified_credentials_are_refused(self, bad: dict[str, Any]) -> None:
        with pytest.raises(CredentialError):
            encrypt_credentials(bad)

    def test_the_model_has_no_plaintext_column(self) -> None:
        """§33 checked against the table itself, not against a comment."""
        columns = {column.name for column in inspect(MarketplaceConnection).columns}
        assert "encrypted_credentials" in columns
        credential_columns = {c for c in columns if "credential" in c}
        assert credential_columns == {"encrypted_credentials"}
        for forbidden in ("plaintext", "password", "api_key", "secret"):
            assert forbidden not in columns, forbidden
        # `token_expires_at` is expiry metadata, not a token.
        assert "token_expires_at" in columns


class TestConnectionStorage:
    @pytest.fixture
    def session(self) -> MagicMock:
        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock(return_value=None)
        return session

    @pytest.fixture
    def tenant(self) -> TenantContext:
        return TenantContext(workspace_id=uuid.uuid4(), user_id=uuid.uuid4())

    async def test_create_connection_stores_ciphertext_only(
        self, session: MagicMock, tenant: TenantContext
    ) -> None:
        connection = await create_connection(
            session,
            tenant,
            marketplace="mock",
            display_name="Demo store",
            credentials=CREDENTIALS,
            granted_scopes=("products:read",),
        )

        assert connection.status is ConnectionStatus.PENDING
        assert connection.granted_scopes == ["products:read"]
        assert CREDENTIALS["api_key"] not in connection.encrypted_credentials
        assert decrypt_credentials(connection.encrypted_credentials) == CREDENTIALS

        stored: MarketplaceConnection = session.add.call_args[0][0]
        assert stored is connection
        assert stored.workspace_id == tenant.workspace_id

    async def test_a_stored_connection_becomes_an_adapter(
        self, session: MagicMock, tenant: TenantContext
    ) -> None:
        connection = await create_connection(
            session,
            tenant,
            marketplace="mock",
            display_name="Demo store",
            credentials=CREDENTIALS,
        )

        assert credentials_for(connection) == CREDENTIALS
        built = connector_for_connection(connection)
        assert isinstance(built, MockConnector)
        assert built.marketplace == connection.marketplace

    async def test_an_unreadable_blob_never_reaches_an_adapter(
        self, session: MagicMock, tenant: TenantContext
    ) -> None:
        connection = await create_connection(
            session,
            tenant,
            marketplace="mock",
            display_name="Demo store",
            credentials=CREDENTIALS,
        )
        connection.encrypted_credentials = "not-a-fernet-token"

        with pytest.raises(CredentialError):
            connector_for_connection(connection)


class TestRollbackHonesty:
    async def test_rollback_restores_the_previous_state(
        self, connector: MockConnector
    ) -> None:
        assert connector.supports_rollback is True
        before = dict((await connector.get_product("p-1")).attributes)
        await connector.update_product("p-1", {"title": "Ruined"})

        restored = await connector.rollback("p-1", before)
        assert restored.attributes == before
        assert "rollback" in connector.calls

    async def test_a_connector_without_rollback_refuses_the_call(self) -> None:
        """§21: do not pretend rollback is possible when it is not."""
        mock = MockConnector(
            products=PRODUCTS, credentials=CREDENTIALS, supports_rollback=False
        )
        with pytest.raises(ConnectorUnsupportedError) as caught:
            await mock.rollback("p-1", {"title": "x"})
        assert caught.value.retryable is False

        other = connector_for("mock", CREDENTIALS)
        assert other.marketplace == "mock"
