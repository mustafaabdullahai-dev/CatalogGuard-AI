"""Tests for edge masking (D-007).

These are the safeguard that keeps proprietary catalog data out of
telemetry. Failures here are security incidents, not cosmetic bugs.
"""

from __future__ import annotations

import pytest

from app.core.masking import MASK, mask_text, mask_value

CATALOG_KEYS = frozenset(
    {"sku", "price", "cost", "supplier_cost", "margin", "unreleased_description"}
)


class TestMaskValue:
    def test_masks_direct_key_match(self) -> None:
        result = mask_value({"sku": "SHIRT-001", "title": "Blue Shirt"}, CATALOG_KEYS)
        assert result["sku"] == MASK
        assert result["title"] == "Blue Shirt"

    def test_masks_substring_variants(self) -> None:
        # camelCase and suffixed variants must not slip through.
        result = mask_value(
            {"supplierCost": "12.50", "unit_price": "29.99", "productSkuId": "X1"},
            CATALOG_KEYS,
        )
        assert result["supplierCost"] == MASK
        assert result["unit_price"] == MASK
        assert result["productSkuId"] == MASK

    @pytest.mark.parametrize("key", ["password", "api_key", "apiKey", "Authorization"])
    def test_masks_secret_names_without_config(self, key: str) -> None:
        assert mask_value({key: "sensitive"})[key] == MASK

    def test_nested_structures(self) -> None:
        result = mask_value(
            {"products": [{"sku": "A1", "color": "Navy"}, {"sku": "A2", "color": "Blue"}]},
            CATALOG_KEYS,
        )
        assert [p["sku"] for p in result["products"]] == [MASK, MASK]
        assert [p["color"] for p in result["products"]] == ["Navy", "Blue"]

    def test_recurses_into_tuples_and_sets(self) -> None:
        # Position within a sequence carries no key, so key-based masking
        # cannot apply. Only secret-shaped values are caught here. Callers
        # must pass keyed structures to mask catalog fields.
        assert mask_value(("SHIRT-001", "Blue"), {"sku"}) == ["SHIRT-001", "Blue"]
        assert mask_value(({"sku": "A1"},), {"sku"}) == [{"sku": MASK}]

    def test_preserves_non_string_scalars(self) -> None:
        assert mask_value({"count": 42, "ratio": 0.97}) == {"count": 42, "ratio": 0.97}

    def test_none_is_safe(self) -> None:
        assert mask_value(None) is None

    def test_long_opaque_tokens_masked_regardless_of_key(self) -> None:
        # An API key pasted into an innocuous field must still be caught.
        leaked = "gsk_" + "A" * 48
        assert mask_value({"note": leaked})["note"] == MASK

    def test_empty_config_masks_nothing_extra(self) -> None:
        assert mask_value({"color": "Navy"}, frozenset()) == {"color": "Navy"}


class TestMaskText:
    def test_masks_key_value_pairs_in_free_text(self) -> None:
        text = "Reconciliation failed for supplier_cost: 4.25 at line 88"
        result = mask_text(text, CATALOG_KEYS)
        assert "4.25" not in result
        assert "line 88" in result

    def test_case_insensitive(self) -> None:
        assert "9.99" not in mask_text("PRICE: 9.99", {"price"})

    def test_preserves_prose(self) -> None:
        prose = "Amazon listing is missing the GTIN attribute"
        assert mask_text(prose, CATALOG_KEYS) == prose