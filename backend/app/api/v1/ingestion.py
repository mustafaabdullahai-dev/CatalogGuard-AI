"""Catalog ingestion endpoints (Phase 10, §49).

Four routes in the order an operator uses them: define a source, upload a file
into it, list sources, inspect the raw records a source delivered. Ingestion is
API-first; the frontend ingestion screen (Phase 21) is a thin client over this.

Every route is gated by `require_ingestion_authority`, which resolves to `write`
today. That mapping is a policy still open (see the dependency's docstring), and
it is concentrated in one place so changing it is one line.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantScope, require_ingestion_authority
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.catalog import CatalogSource, SourceRecord
from app.schemas.ingestion import (
    CatalogSourceCreate,
    CatalogSourceListResponse,
    CatalogSourceRef,
    SchemaModel,
    SourceRecordListResponse,
    SourceRecordRef,
    UploadResponse,
)
from app.services.ingestion import pipeline
from app.services.ingestion.errors import IngestionError, IngestionErrorCode
from app.services.ingestion.repositories import (
    CatalogSourceRepository,
    SourceRecordRepository,
)

router = APIRouter(tags=["ingestion"])
logger = get_logger(__name__)

# §54 code -> HTTP status. An unsupported media type is a 415 and an oversized
# file a 413 because both are the client's to fix and have standard meanings; a
# malformed or unsafe file is a 422, since the request was understood and
# deliberately refused.
_ERROR_STATUS: dict[IngestionErrorCode, int] = {
    IngestionErrorCode.UNSUPPORTED_FORMAT: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    IngestionErrorCode.FILE_TOO_LARGE: status.HTTP_413_CONTENT_TOO_LARGE,
    IngestionErrorCode.STORAGE_FAILED: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def _http_error(exc: IngestionError) -> HTTPException:
    return HTTPException(
        status_code=_ERROR_STATUS.get(
            exc.code, status.HTTP_422_UNPROCESSABLE_CONTENT
        ),
        detail=exc.as_dict(),
    )


@router.post(
    "/catalog-sources",
    response_model=CatalogSourceRef,
    status_code=status.HTTP_201_CREATED,
    summary="Define a catalog source",
)
async def create_catalog_source(
    body: CatalogSourceCreate,
    scope: Annotated[TenantScope, Depends(require_ingestion_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CatalogSourceRef:
    """Create the feed that a file will be uploaded into."""
    repo = CatalogSourceRepository(db, scope.tenant)
    source = CatalogSource(name=body.name, source_type=body.source_type)
    await repo.add(source)
    await db.commit()
    logger.info(
        "catalog_source_created",
        workspace_id=str(scope.workspace_id),
        source_id=str(source.id),
        source_type=str(body.source_type),
    )
    return CatalogSourceRef.from_record(source)


@router.get(
    "/catalog-sources",
    response_model=CatalogSourceListResponse,
    summary="List catalog sources with record counts",
)
async def list_catalog_sources(
    scope: Annotated[TenantScope, Depends(require_ingestion_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> CatalogSourceListResponse:
    repo = CatalogSourceRepository(db, scope.tenant)
    sources = await repo.list(limit=200, order_by=CatalogSource.created_at)

    counts: dict[UUID, int] = {}
    ids = [source.id for source in sources]
    if ids:
        # One grouped query rather than a count per source: the list endpoint is
        # the hot path, and N+1 here is N round trips to a pooled database.
        rows = await db.execute(
            select(SourceRecord.catalog_source_id, func.count())
            .where(SourceRecord.workspace_id == scope.workspace_id)
            .where(SourceRecord.catalog_source_id.in_(ids))
            .group_by(SourceRecord.catalog_source_id)
        )
        counts = {row[0]: row[1] for row in rows.all()}

    return CatalogSourceListResponse(
        sources=[
            CatalogSourceRef.from_record(source, record_count=counts.get(source.id, 0))
            for source in sources
        ]
    )


@router.post(
    "/catalog-sources/{source_id}/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a catalog file into a source",
)
async def upload_catalog_file(
    source_id: UUID,
    scope: Annotated[TenantScope, Depends(require_ingestion_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File(description="CSV, JSON or XLSX file")],
) -> UploadResponse:
    """Validate, parse and store a file. Nothing is normalized here."""
    repo = CatalogSourceRepository(db, scope.tenant)
    source = await repo.get(source_id)
    if source is None:
        # Scoped by workspace in the repository, so another tenant's source id
        # is a 404 rather than a cross-workspace write.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Catalog source not found"
        )

    filename = file.filename or "upload"
    content = await file.read()
    try:
        outcome = await pipeline.ingest(
            db,
            scope.tenant,
            filename=filename,
            content=content,
            source=source,
        )
    except IngestionError as exc:
        raise _http_error(exc) from exc
    await db.commit()

    persisted = outcome.persisted
    return UploadResponse(
        source_id=source.id,
        execution=outcome.execution,
        records_inserted=persisted.records_inserted if persisted else 0,
        message_id=outcome.message_id,
        format=outcome.prepared.parsed.format,
        encoding=outcome.prepared.parsed.encoding,
        truncated=outcome.prepared.parsed.truncated,
        scanner=outcome.prepared.validated.scanner,
        warnings=list(outcome.prepared.warnings),
        detected_schema=SchemaModel.from_detection(outcome.prepared.schema),
    )


@router.get(
    "/catalog-sources/{source_id}/records",
    response_model=SourceRecordListResponse,
    summary="Inspect the raw records a source delivered",
)
async def list_source_records(
    source_id: UUID,
    scope: Annotated[TenantScope, Depends(require_ingestion_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SourceRecordListResponse:
    """Raw, pre-normalization records.

    Returned verbatim on purpose. These are what the reconciliation engine will
    read, so a reviewer has to be able to see exactly what arrived rather than a
    normalized projection that has already made choices.
    """
    source_repo = CatalogSourceRepository(db, scope.tenant)
    if not await source_repo.exists(source_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Catalog source not found"
        )

    record_repo = SourceRecordRepository(db, scope.tenant)
    where = SourceRecord.catalog_source_id == source_id
    records = await record_repo.list(
        limit=limit, offset=offset, order_by=SourceRecord.external_id, where=where
    )
    total = await record_repo.count(where=where)
    return SourceRecordListResponse(
        records=[SourceRecordRef.from_record(record) for record in records],
        total=total,
        limit=limit,
        offset=offset,
    )

