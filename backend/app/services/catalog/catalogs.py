"""Catalog resolution (Phase 11).

`products.catalog_id` is NOT NULL, but nothing in the system ever created a
`Catalog`, so no product could exist. This resolves -- creating on first use --
the workspace's default catalog, which is the anchor every product hangs from.

Idempotent under concurrency. Two first-ingest requests can both find no catalog
and both insert one; `uq_catalogs_workspace_slug` rejects the loser, and this
re-reads rather than failing the request. The insert runs inside a SAVEPOINT so
losing that race does not poison the caller's transaction -- a rollback of the
whole request would discard the records the caller already validated.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import TenantContext
from app.models.catalog import Catalog
from app.services.catalog.repositories import CatalogRepository

DEFAULT_CATALOG_NAME = "Default catalog"
DEFAULT_CATALOG_SLUG = "default"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """A URL-safe slug. Raises rather than inventing one for an empty name."""
    slug = _SLUG_STRIP.sub("-", value.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot derive a slug from {value!r}")
    return slug[:63]


async def create_catalog(
    db: AsyncSession, tenant: TenantContext, *, name: str, slug: str | None = None
) -> Catalog:
    """Create a named catalog. The slug is derived from the name when omitted."""
    catalog = Catalog(name=name, slug=slugify(slug or name))
    await CatalogRepository(db, tenant).add(catalog)
    return catalog


async def _find_default(db: AsyncSession, tenant: TenantContext) -> Catalog | None:
    result = await db.execute(
        select(Catalog)
        .where(Catalog.workspace_id == tenant.workspace_id)
        .where(Catalog.slug == DEFAULT_CATALOG_SLUG)
        .where(Catalog.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def resolve_default_catalog(
    db: AsyncSession, tenant: TenantContext, *, create: bool = True
) -> Catalog | None:
    """Return the workspace's default catalog, creating it if `create`."""
    existing = await _find_default(db, tenant)
    if existing is not None:
        return existing
    if not create:
        return None

    catalog = Catalog(name=DEFAULT_CATALOG_NAME, slug=DEFAULT_CATALOG_SLUG)
    try:
        async with db.begin_nested():
            await CatalogRepository(db, tenant).add(catalog)
    except IntegrityError:
        existing = await _find_default(db, tenant)
        if existing is None:
            # The conflict was not the slug we expected. Re-raise rather than
            # invent a catalog, so an unexpected constraint failure stays loud.
            raise
        return existing
    return catalog