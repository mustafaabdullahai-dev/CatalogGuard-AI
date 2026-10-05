"""Canonical product endpoints (Phase 11, §7, §8, §10).

Read side of the canonical model (catalogs, products) plus the pass that builds
it: `POST /catalog-sources/{id}/canonicalize` folds a source's raw records into
canonical products.

It lives here rather than in the ingestion router because it depends on the
catalog domain, not on file handling. Ingestion ends at `source_records`; this is
the next stage, and keeping the two routers apart keeps that boundary visible.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantScope, require_catalog_authority
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.catalog import Catalog, Product
from app.schemas.catalog import (
    CanonicalizationResponse,
    CatalogCreate,
    CatalogListResponse,
    CatalogRef,
    MergeCandidateRef,
    ProductListResponse,
    ProductRef,
)
from app.services.catalog import canonicalization, catalogs
from app.services.catalog.repositories import (
    CatalogRepository,
    CatalogSourceRepository,
    ProductRepository,
)

router = APIRouter(tags=["catalog"])
logger = get_logger(__name__)

_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="Catalog source not found"
)


@router.get("/catalogs", response_model=CatalogListResponse, summary="List catalogs")
async def list_catalogs(
    scope: Annotated[TenantScope, Depends(require_catalog_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CatalogListResponse:
    repo = CatalogRepository(db, scope.tenant)
    records = await repo.list(limit=100, order_by=Catalog.created_at)
    return CatalogListResponse(catalogs=[CatalogRef.from_record(row) for row in records])


@router.post(
    "/catalogs",
    response_model=CatalogRef,
    status_code=status.HTTP_201_CREATED,
    summary="Create a catalog",
)
async def create_catalog(
    body: CatalogCreate,
    scope: Annotated[TenantScope, Depends(require_catalog_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CatalogRef:
    try:
        catalog = await catalogs.create_catalog(
            db, scope.tenant, name=body.name, slug=body.slug
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except IntegrityError as exc:
        # Same slug in the same workspace. `uq_catalogs_workspace_slug` is the
        # authority; this turns its complaint into an answer a caller can act on.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A catalog with that slug already exists in this workspace",
        ) from exc
    await db.commit()
    return CatalogRef.from_record(catalog)


@router.post(
    "/catalog-sources/{source_id}/canonicalize",
    response_model=CanonicalizationResponse,
    summary="Fold a source's raw records into canonical products",
)
async def canonicalize_source(
    source_id: UUID,
    scope: Annotated[TenantScope, Depends(require_catalog_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=50_000)] = canonicalization.DEFAULT_BATCH,
) -> CanonicalizationResponse:
    """Run the canonicalization pass over one source.

    Resumable and idempotent: a second call processes only the records that have
    no product yet, so re-running after a mapping fix is safe.
    """
    source = await CatalogSourceRepository(db, scope.tenant).get(source_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Catalog source not found"
        )

    catalog = await catalogs.resolve_default_catalog(db, scope.tenant)
    if catalog is None:  # unreachable with create=True; kept for the type check
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not resolve a catalog for this workspace",
        )

    summary = await canonicalization.canonicalize_source(
        db, scope.tenant, source=source, catalog=catalog, limit=limit
    )
    await db.commit()

    return CanonicalizationResponse(
        source_id=source.id,
        catalog_id=catalog.id,
        records_examined=summary.records_examined,
        products_created=summary.products_created,
        products_matched=summary.products_matched,
        identifiers_added=summary.identifiers_added,
        versions_written=summary.versions_written,
        unmapped_records=summary.unmapped_records,
        unmapped_columns=list(summary.unmapped_columns),
        merge_candidates=[
            MergeCandidateRef(
                created_product_id=UUID(candidate.created_product_id),
                candidate_product_id=(
                    UUID(candidate.candidate_product_id)
                    if candidate.candidate_product_id
                    else None
                ),
                confidence=candidate.confidence,
                kind=candidate.kind,
                reason=candidate.reason,
                evidence=list(candidate.evidence),
            )
            for candidate in summary.merge_candidates
        ],
        note=(
            "Records are normalized and matched by exact identifier only. "
            "Ambiguous matches are recorded as identity conflicts for review. "
            "Field-level resolution between disagreeing sources is the "
            "reconciliation engine (Phase 12)."
        ),
    )


@router.get(
    "/products",
    response_model=ProductListResponse,
    summary="List canonical products",
)
async def list_products(
    scope: Annotated[TenantScope, Depends(require_catalog_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProductListResponse:
    repo = ProductRepository(db, scope.tenant)
    records = await repo.list(limit=limit, offset=offset, order_by=Product.sku)
    total = await repo.count()
    return ProductListResponse(
        products=[ProductRef.from_record(row) for row in records],
        total=total,
        limit=limit,
        offset=offset,
    )
