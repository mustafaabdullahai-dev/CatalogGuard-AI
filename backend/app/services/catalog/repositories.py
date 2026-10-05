"""Tenant-scoped repositories for the canonical product model (D-011).

Small subclasses that bind a model once, so no call site repeats it and every
read or write of these tables goes through a `TenantRepository`.
"""

from __future__ import annotations

from app.db.repository import TenantRepository
from app.models.catalog import (
    Catalog,
    CatalogSource,
    Product,
    ProductIdentifier,
    ProductVersion,
    SourceRecord,
)
from app.models.reconciliation import Conflict


class CatalogRepository(TenantRepository[Catalog]):
    model = Catalog


class CatalogSourceRepository(TenantRepository[CatalogSource]):
    model = CatalogSource


class SourceRecordRepository(TenantRepository[SourceRecord]):
    model = SourceRecord


class ProductRepository(TenantRepository[Product]):
    model = Product


class ProductIdentifierRepository(TenantRepository[ProductIdentifier]):
    model = ProductIdentifier


class ProductVersionRepository(TenantRepository[ProductVersion]):
    model = ProductVersion


class ConflictRepository(TenantRepository[Conflict]):
    model = Conflict