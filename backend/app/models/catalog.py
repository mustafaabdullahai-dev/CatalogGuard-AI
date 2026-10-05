"""Catalog domain models.

Canonical product model (Section 10), source records, and parent-child
relationships (Section 9).

Two structural choices worth stating:

1. **Composite foreign keys pair `workspace_id` with the parent id.** A
   `product_id` reference therefore cannot point at another tenant's row even
   if application code gets the tenant filter wrong. This is the database
   enforcing isolation, not the application.

2. **Product data is versioned, not overwritten** (D-012, Section 50). The
   `products` row holds current state for fast reads; `product_versions`
   holds immutable history. Reconciliation runs bind to a version so a
   finding can always be replayed against the catalog state it saw.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import ImmutableTenantEntity, SoftDeletableTenantEntity, str_enum


class ProductStatus(enum.StrEnum):
    ACTIVE = "active"
    DRAFT = "draft"
    ARCHIVED = "archived"
    SUPPRESSED = "suppressed"


class RelationshipType(enum.StrEnum):
    PARENT = "parent"
    VARIANT = "variant"
    BUNDLE = "bundle"
    REPLACEMENT = "replacement"
    ACCESSORY = "accessory"


class SourceType(enum.StrEnum):
    """D-002 source authority tiers.

    Ordered by the default precedence. A tenant may override via
    `workspace.source_authority_config`, so this ordering is a default, not
    a hard rule.
    """

    MANUFACTURER = "manufacturer"
    PIM = "pim"
    ERP = "erp"
    WMS = "wms"
    INTERNAL = "internal"
    MARKETPLACE = "marketplace"
    HISTORICAL = "historical"
    AI_INFERRED = "ai_inferred"

    @property
    def default_rank(self) -> int:
        return _SOURCE_RANKS[self]


# Default precedence (D-002). Overridable per workspace via
# `workspace.source_authority_config`, so this is a default, not a hard rule.
#
# ERP, PIM, and WMS share rank 2: which of them wins for a given attribute is
# a tenant policy question, not a universal truth. D-002's matrix resolves it
# per attribute (pricing to ERP, titles to PIM, inventory to WMS).
_SOURCE_RANKS: dict[SourceType, int] = {
    SourceType.MANUFACTURER: 1,
    SourceType.PIM: 2,
    SourceType.ERP: 2,
    SourceType.WMS: 2,
    SourceType.INTERNAL: 4,
    SourceType.MARKETPLACE: 5,
    SourceType.HISTORICAL: 6,
    SourceType.AI_INFERRED: 7,
}


class Catalog(SoftDeletableTenantEntity):
    """A tenant's catalog scope. Reconciliation runs bind to one."""

    __tablename__ = "catalogs"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    # Monotonic version counter. A run references the version it observed.
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_catalogs_workspace_slug"),
        {"comment": "Tenant catalog scope; reconciliation runs reference a version"},
    )

    products: Mapped[list[Product]] = relationship(
        back_populates="catalog", cascade="all, delete-orphan"
    )


class Product(SoftDeletableTenantEntity):
    """Canonical product.

    Holds current state only. History lives in `ProductVersion`.
    """

    __tablename__ = "products"

    # No plain ForeignKey here on purpose. The composite constraint in
    # __table_args__ pairs catalog_id with workspace_id, which is what makes a
    # product incapable of pointing at another tenant's catalog. Adding a
    # second, single-column FK to catalogs.id would reintroduce a path that
    # bypasses the tenant pairing and makes the ORM relationship ambiguous.
    catalog_id: Mapped[UUID] = mapped_column(nullable=False)
    sku: Mapped[str] = mapped_column(String(255), nullable=False)
    mpn: Mapped[str | None] = mapped_column(String(255), nullable=True)
    gtin: Mapped[str | None] = mapped_column(String(64), nullable=True)
    upc: Mapped[str | None] = mapped_column(String(64), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    manufacturer: Mapped[str | None] = mapped_column(String(255), nullable=True)

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[ProductStatus] = mapped_column(
        str_enum(ProductStatus, name="product_status", length=32),
        default=ProductStatus.ACTIVE,
        nullable=False,
    )
    is_parent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Canonical attribute bag. JSONB because attribute sets vary by category
    # and marketplace; normalized columns exist only for fields that are
    # queried, filtered, or constrained.
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    media: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    catalog: Mapped[Catalog] = relationship(back_populates="products")

    # Self-referential hierarchy. `remote_side` belongs on the many-to-one
    # side only; putting it on both sides makes SQLAlchemy read both as
    # many-to-one and refuse to configure the mapper.
    variants: Mapped[list[Product]] = relationship(
        back_populates="parent",
        # The parent FK also carries workspace_id, which the catalog
        # relationship already writes. Declared overlap: the parent FK must
        # not overwrite tenant on load.
        overlaps="catalog,products",
    )

    # Composite FK, not a plain one: a variant must belong to the same tenant
    # as its parent.
    parent_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    parent: Mapped[Product | None] = relationship(
        back_populates="variants",
        remote_side="Product.id",
        foreign_keys="Product.parent_id",
    )

    __table_args__ = (
        # SKU is unique per workspace, not globally: two tenants may legitimately
        # use the same SKU. Cross-tenant duplicate SKUs must not collide.
        UniqueConstraint("workspace_id", "sku", name="uq_products_workspace_sku"),
        ForeignKeyConstraint(
            ["workspace_id", "catalog_id"],
            ["catalogs.workspace_id", "catalogs.id"],
            name="fk_products_workspace_catalog",
            ondelete="CASCADE",
        ),
        Index("ix_products_workspace_status", "workspace_id", "status"),
        Index("ix_products_workspace_gtin", "workspace_id", "gtin"),
        Index("ix_products_workspace_parent", "workspace_id", "parent_id"),
        # Variant/parent hierarchy stays within the tenant.
        #
        # RESTRICT rather than SET NULL, and not because ON DELETE SET NULL is
        # unavailable: a composite SET NULL nulls every referencing column,
        # which would include the NOT NULL workspace_id. Postgres can target one
        # column (`SET NULL (parent_id)`, PG15+), but the requirement that
        # deleting a parent must not silently delete a variant that is a real
        # listing in its own right is better served by refusing the delete. The
        # normal path is soft delete, which this constraint never sees.
        ForeignKeyConstraint(
            ["workspace_id", "parent_id"],
            ["products.workspace_id", "products.id"],
            name="fk_products_workspace_parent",
            ondelete="RESTRICT",
        ),
        # A product cannot be its own parent.
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name="not_self_parent"
        ),
        {"comment": "Canonical product, current state only"},
    )


class ProductVersion(ImmutableTenantEntity):
    """Immutable product snapshot (Section 50, D-012).

    Written on every reconciliation that produces a change, so a proposal
    can always be replayed against the exact catalog state it observed.
    """

    __tablename__ = "product_versions"

    product_id: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reconciliation_run_id: Mapped[UUID | None] = mapped_column(nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_product_versions_workspace_product",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id", "product_id", "version", name="uq_product_versions_number"
        ),
        {"comment": "Immutable product history for audit and replay"},
    )


class ProductIdentifier(SoftDeletableTenantEntity):
    """External identifiers used for identity resolution (Section 8).

    GTIN check-digit validity is validated deterministically before any
    identity claim is made from an identifier.
    """

    __tablename__ = "product_identifiers"

    product_id: Mapped[UUID] = mapped_column(nullable=False)
    identifier_type: Mapped[str] = mapped_column(String(32), nullable=False)
    identifier_value: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        str_enum(SourceType, name="source_type", length=32), nullable=False
    )
    source_record_id: Mapped[UUID | None] = mapped_column(nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_identifiers_workspace_product",
            ondelete="CASCADE",
        ),
        # An identifier may appear in multiple source records, but not twice
        # within the same source: that is an ingestion defect worth surfacing.
        UniqueConstraint(
            "workspace_id",
            "identifier_type",
            "identifier_value",
            "source_record_id",
            name="uq_identifiers_scope",
        ),
        Index("ix_identifiers_lookup", "workspace_id", "identifier_type", "identifier_value"),
        {"comment": "External identifiers for deterministic identity resolution"},
    )


class ProductRelationship(SoftDeletableTenantEntity):
    """Parent-child and family relationships (Section 9).

    Relationships are never modified without deterministic validation and
    human approval. This table is the record of both the relationship and
    how it came to exist.
    """

    __tablename__ = "product_relationships"

    parent_id: Mapped[UUID] = mapped_column(nullable=False)
    child_id: Mapped[UUID] = mapped_column(nullable=False)
    relationship_type: Mapped[RelationshipType] = mapped_column(
        str_enum(RelationshipType, name="relationship_type", length=32), nullable=False
    )
    # Why this relationship exists, and who sanctioned it.
    established_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approval_reference: Mapped[UUID | None] = mapped_column(nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "parent_id"],
            ["products.workspace_id", "products.id"],
            name="fk_rel_workspace_parent",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "child_id"],
            ["products.workspace_id", "products.id"],
            name="fk_rel_workspace_child",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id", "parent_id", "child_id", name="uq_relationship_pair"
        ),
        Index("ix_relationships_workspace_parent", "workspace_id", "parent_id"),
        Index("ix_relationships_workspace_child", "workspace_id", "child_id"),
        # A product cannot be its own parent. Section 9 lists invalid parents
        # as a defect class, so the database rejects the obvious case.
        CheckConstraint("parent_id <> child_id", name="not_self"),
        {"comment": "Product family structure; always approval-gated"},
    )


class CatalogSource(SoftDeletableTenantEntity):
    """An ingested data source (Section 49)."""

    __tablename__ = "catalog_sources"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        str_enum(SourceType, name="source_type", length=32), nullable=False
    )
    # Detected schema, retained so a re-upload can be compared against it.
    detected_schema: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # D-002 "stamp": freshness and authorization context for every packet.
    last_sequence_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    authorization_context: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )

    __table_args__ = (
        Index("ix_catalog_sources_workspace_type", "workspace_id", "source_type"),
        {"comment": "Ingested data source with freshness stamp"},
    )


class SourceRecord(SoftDeletableTenantEntity):
    """A raw record as it arrived, before normalization.

    Retained verbatim so normalization and conflict decisions can be
    re-derived if the rules change.
    """

    __tablename__ = "source_records"

    catalog_source_id: Mapped[UUID] = mapped_column(nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sequence_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Set once the record has been folded into a canonical product (Phase 11).
    # NULL means "not yet canonicalized", which is what makes the pass idempotent:
    # a re-run selects only the rows that have no product yet, so re-uploading the
    # same feed cannot duplicate a catalog.
    product_id: Mapped[UUID | None] = mapped_column(nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "catalog_source_id"],
            ["catalog_sources.workspace_id", "catalog_sources.id"],
            name="fk_source_records_workspace_source",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_source_records_workspace_product",
            ondelete="SET NULL",
        ),
        UniqueConstraint(
            "workspace_id", "catalog_source_id", "external_id", name="uq_source_record"
        ),
        Index("ix_source_records_workspace_source", "workspace_id", "catalog_source_id"),
        Index("ix_source_records_workspace_product", "workspace_id", "product_id"),
        {"comment": "Raw ingested record, pre-normalization"},
    )


class MarketplaceProduct(SoftDeletableTenantEntity):
    """A product's representation on a marketplace (Section 22).

    Kept separate from `products` because channel listings are partial,
    constrained, and frequently stale relative to canonical truth.
    """

    __tablename__ = "marketplace_products"

    product_id: Mapped[UUID | None] = mapped_column(nullable=True)
    marketplace: Mapped[str] = mapped_column(String(64), nullable=False)
    external_product_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_sku: Mapped[str | None] = mapped_column(String(255), nullable=True)
    listing_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_listing: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # URL and handle changes carry SEO risk (Section 17), so they are tracked.
    listing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    handle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_mkt_products_workspace_product",
            ondelete="SET NULL",
        ),
        UniqueConstraint(
            "workspace_id",
            "marketplace",
            "external_product_id",
            name="uq_marketplace_product",
        ),
        Index("ix_mkt_products_workspace_marketplace", "workspace_id", "marketplace"),
        Index("ix_mkt_products_workspace_product", "workspace_id", "product_id"),
        {"comment": "Channel listing, separate from canonical product"},
    )


class HealthScore(SoftDeletableTenantEntity):
    """Explainable catalog health (Section 53).

    Every score stores the underlying issue counts, because a score the user
    cannot interrogate is a vanity metric.
    """

    __tablename__ = "health_scores"

    catalog_id: Mapped[UUID] = mapped_column(nullable=False)
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    data_quality: Mapped[float] = mapped_column(Float, nullable=False)
    consistency: Mapped[float] = mapped_column(Float, nullable=False)
    compliance: Mapped[float] = mapped_column(Float, nullable=False)
    relationship_integrity: Mapped[float] = mapped_column(Float, nullable=False)
    seo_readiness: Mapped[float] = mapped_column(Float, nullable=False)
    change_safety: Mapped[float] = mapped_column(Float, nullable=False)
    # The counts behind each dimension. This is what makes it explainable.
    issue_counts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "catalog_id"],
            ["catalogs.workspace_id", "catalogs.id"],
            name="fk_health_workspace_catalog",
            ondelete="CASCADE",
        ),
        Index("ix_health_workspace_catalog_time", "workspace_id", "catalog_id", "computed_at"),
        {"comment": "Explainable health score with underlying issue counts"},
    )