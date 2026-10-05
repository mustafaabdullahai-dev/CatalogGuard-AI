"""Canonical product endpoints (Phase 11).

Read side of the canonical model: catalogs and products. The write side is
`POST /catalog-sources/{id}/canonicalize`, which is the pass that turns raw
records into products; it lives here rather than in the ingestion router because
it depends on the catalog domain, not on file handling.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CatalogRef(BaseModel):
    id: UUID
    name: str
    slug: str
    current_version: int
    created_at: datetime

    @classmethod
    def from_record(cls, catalog: Any) -> CatalogRef:
        return cls(
            id=catalog.id,
            name=catalog.name,
            slug=catalog.slug,
            current_version=catalog.current_version,
            created_at=catalog.created_at,
        )


class CatalogListResponse(BaseModel):
    catalogs: list[CatalogRef]


class CatalogCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(
        default=None,
        max_length=255,
        description="Optional URL-safe slug; derived from the name when omitted.",
    )


class ProductRef(BaseModel):
    """A canonical product.

    Field-by-field rather than a dump: `attributes` and `media` are the two bags
    a future migration is most likely to extend with something that should not
    be public.
    """

    id: UUID
    catalog_id: UUID
    sku: str
    gtin: str | None
    upc: str | None
    mpn: str | None
    brand: str | None
    manufacturer: str | None
    title: str | None
    category_path: str | None
    status: str
    version: int
    is_parent: bool
    attributes: dict[str, Any]
    last_reconciled_at: datetime | None
    created_at: datetime

    @classmethod
    def from_record(cls, product: Any) -> ProductRef:
        return cls(
            id=product.id,
            catalog_id=product.catalog_id,
            sku=product.sku,
            gtin=product.gtin,
            upc=product.upc,
            mpn=product.mpn,
            brand=product.brand,
            manufacturer=product.manufacturer,
            title=product.title,
            category_path=product.category_path,
            status=str(product.status),
            version=product.version,
            is_parent=product.is_parent,
            attributes=dict(product.attributes or {}),
            last_reconciled_at=product.last_reconciled_at,
            created_at=product.created_at,
        )


class ProductListResponse(BaseModel):
    products: list[ProductRef]
    total: int
    limit: int
    offset: int


class MergeCandidateRef(BaseModel):
    """A pair the identity resolver refused to merge on its own (D-015)."""

    created_product_id: UUID
    candidate_product_id: UUID | None
    confidence: float
    kind: str
    reason: str
    evidence: list[str]


class CanonicalizationResponse(BaseModel):
    source_id: UUID
    catalog_id: UUID
    records_examined: int
    products_created: int
    products_matched: int
    identifiers_added: int
    versions_written: int
    unmapped_records: int
    unmapped_columns: list[str]
    merge_candidates: list[MergeCandidateRef]
    note: str