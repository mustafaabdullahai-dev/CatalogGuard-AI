"""Canonicalization tests (Phase 11).

The pure half of the pass -- mapping a record onto canonical fields, choosing a
deterministic SKU, projecting a product back to an identity candidate, and
snapshotting it -- is provable from data alone, so these run with no database.

The scenarios are the ones that decide whether a catalogue stays trustworthy:
`Product Name`/`Colour` mapping to the right targets, an unmapped column being
reported rather than guessed into a canonical field, and the SKU fallback being
stable so a re-run cannot fork one product into two.
"""

from __future__ import annotations

from uuid import UUID

import httpx
from httpx import ASGITransport

from app.main import app
from app.models.catalog import ProductStatus, SourceType
from app.services.catalog.canonicalization import (
    build_product,
    deterministic_sku,
    map_record,
    product_to_candidate,
    snapshot,
)

SOURCE_ID = UUID("11111111-1111-1111-1111-111111111111")
CATALOG_ID = UUID("22222222-2222-2222-2222-222222222222")


class TestMapRecord:
    def test_columns_and_attributes_are_split(self) -> None:
        mapping = map_record(
            {
                "Product Name": "Navy Tee",
                "Colour": "Navy Blue",
                "Unit Price": "19.99",
                "gtin13": "4006381333931",
            },
            external_id="row-1",
            source_type=SourceType.MANUFACTURER,
        )
        assert mapping.columns["title"] == "Navy Tee"
        assert mapping.attributes["color"] == "Navy Blue"
        assert str(mapping.attributes["price"]) == "19.99"
        # `normalize_identifier` zero-pads GS1 codes to 14 digits, so an EAN-13
        # and its GTIN-14 spelling compare equal (D-015).
        assert mapping.candidate.gtin == "04006381333931"
        assert mapping.candidate.source is SourceType.MANUFACTURER

    def test_unmapped_column_is_reported_not_guessed(self) -> None:
        mapping = map_record(
            {"Weird Vendor Column": "x", "title": "Widget"},
            external_id="row-1",
            source_type=SourceType.INTERNAL,
        )
        assert "Weird Vendor Column" in mapping.unknown_fields
        assert "Weird Vendor Column" not in mapping.columns
        assert "Weird Vendor Column" not in mapping.attributes

    def test_image_url_lands_in_media(self) -> None:
        mapping = map_record(
            {"image_url": "https://example.test/a.png"},
            external_id="row-1",
            source_type=SourceType.INTERNAL,
        )
        assert mapping.media["images"] == ["https://example.test/a.png"]

    def test_blank_values_do_not_become_identifiers(self) -> None:
        mapping = map_record(
            {"sku": "   ", "gtin": ""},
            external_id="row-1",
            source_type=SourceType.INTERNAL,
        )
        assert mapping.candidate.sku is None
        assert mapping.candidate.gtin is None


class TestDeterministicSku:
    def test_explicit_sku_wins(self) -> None:
        mapping = map_record(
            {"sku": "ABC", "gtin": "4006381333931"},
            external_id="r1",
            source_type=SourceType.INTERNAL,
        )
        assert deterministic_sku(mapping, SOURCE_ID) == "ABC"

    def test_falls_back_to_an_identifier(self) -> None:
        mapping = map_record(
            {"gtin": "4006381333931"},
            external_id="r1",
            source_type=SourceType.INTERNAL,
        )
        assert deterministic_sku(mapping, SOURCE_ID) == "04006381333931"

    def test_fallback_is_namespaced_and_stable(self) -> None:
        mapping = map_record(
            {"title": "Widget"}, external_id="row-7", source_type=SourceType.INTERNAL
        )
        first = deterministic_sku(mapping, SOURCE_ID)
        assert first == deterministic_sku(mapping, SOURCE_ID)
        assert first.startswith("auto-11111111-")
        assert first.endswith("row-7")


class TestProductProjection:
    def test_build_product_maps_every_column(self) -> None:
        mapping = map_record(
            {
                "sku": "ABC",
                "Product Name": "Navy Tee",
                "Colour": "Navy",
                "gtin13": "4006381333931",
            },
            external_id="r1",
            source_type=SourceType.MANUFACTURER,
        )
        product = build_product(mapping, catalog_id=CATALOG_ID, sku="ABC")
        assert product.catalog_id == CATALOG_ID
        assert product.title == "Navy Tee"
        assert product.gtin == "04006381333931"
        assert product.attributes == {"color": "Navy"}
        assert product.version == 1
        # Set at construction, so the v1 snapshot cannot record `None`.
        assert product.status is ProductStatus.ACTIVE

    def test_snapshot_captures_the_state_it_was_taken_at(self) -> None:
        mapping = map_record(
            {"sku": "ABC", "Product Name": "Navy Tee"},
            external_id="r1",
            source_type=SourceType.INTERNAL,
        )
        product = build_product(mapping, catalog_id=CATALOG_ID, sku="ABC")
        snap = snapshot(product)
        assert snap["sku"] == "ABC"
        assert snap["title"] == "Navy Tee"
        assert snap["version"] == 1
        assert snap["status"] == "active"

    def test_product_projects_back_to_a_candidate(self) -> None:
        mapping = map_record(
            {"sku": "ABC", "gtin13": "4006381333931", "Colour": "Navy"},
            external_id="r1",
            source_type=SourceType.INTERNAL,
        )
        product = build_product(mapping, catalog_id=CATALOG_ID, sku="ABC")
        candidate = product_to_candidate(product)
        assert candidate.product_id is not None
        assert candidate.gtin == "04006381333931"
        assert candidate.sku == "ABC"


class TestCatalogApi:
    async def test_routes_are_in_the_spec(self) -> None:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            paths = (await client.get("/openapi.json")).json()["paths"]
        assert "/api/v1/catalogs" in paths
        assert "/api/v1/catalog-sources/{source_id}/canonicalize" in paths
        assert "/api/v1/products" in paths

    async def test_listing_requires_a_session(self) -> None:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/catalogs")
        assert response.status_code == 401
