"""Compliance knowledge endpoints (Phase 13, §13, §14).

Three routes: add a policy document, list them, and search them.

The search route is the one that matters. §13 requires that a recommendation be
able to point at the exact rule supporting it, so the response carries a citation
per hit -- document, section reference, marketplace -- and states which retrieval
signals produced the ranking. A hit found only by keyword overlap is a different
kind of evidence from one found by vector similarity, and §37's warning about
confidence applies here too: neither is certainty, and the difference between
them is the reviewer's to weigh.

Nothing on this router decides compliance. §16 and §5 are explicit that risk and
validity are deterministic, and D-005 confirmed there is no rule-cited compliance
engine in the MVP: these routes return evidence that a later phase will consume,
and the UI must label early compliance findings as heuristic rather than
authoritative.

All three routes are gated on `write` (D-021, resolved); the reasoning and the
rejected alternative live on `require_compliance_authority`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantScope, require_compliance_authority
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.platform import ComplianceChunk, ComplianceDocument
from app.rag import service
from app.rag.repositories import ComplianceDocumentRepository
from app.schemas.compliance import (
    ComplianceDocumentCreate,
    ComplianceDocumentListResponse,
    ComplianceDocumentRef,
    ComplianceIngestResponse,
    ComplianceSearchResponse,
)

router = APIRouter(tags=["compliance"])
logger = get_logger(__name__)


@router.post(
    "/compliance/documents",
    response_model=ComplianceIngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a policy document to the compliance corpus",
)
async def add_compliance_document(
    payload: ComplianceDocumentCreate,
    scope: Annotated[TenantScope, Depends(require_compliance_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ComplianceIngestResponse:
    """Chunk, optionally embed, and store a policy document.

    Idempotent on content hash: re-posting an identical document returns 201 with
    `duplicate=true` and writes nothing. That matters for the normal operating
    pattern -- a scheduled job refetching every marketplace's rules -- which would
    otherwise accumulate copies of the same rule until one duplicate outvotes the
    sources that agree with it.
    """
    try:
        result = await service.ingest_document(
            db,
            scope.tenant,
            title=payload.title,
            content=payload.content,
            source_url=payload.source_url,
            marketplace_code=payload.marketplace_code,
            country_code=payload.country_code,
            category_path=payload.category_path,
            document_kind=payload.document_kind,
            rule_kind=payload.rule_kind,
            version_label=payload.version_label,
            effective_date=payload.effective_date,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await db.commit()

    if result.duplicate:
        note = (
            "An identical document already exists in this workspace. Nothing was "
            "written; the existing document id is returned."
        )
    elif result.indexed_semantically:
        note = (
            f"{result.chunks_written} chunks stored and semantically indexed. "
            "Search will use hybrid retrieval."
        )
    else:
        note = (
            f"{result.chunks_written} chunks stored WITHOUT vectors: no embedding "
            "provider is configured. Search will use keyword and metadata evidence "
            "only, and results will say so."
        )

    return ComplianceIngestResponse.from_result(result, note=note)


@router.get(
    "/compliance/documents",
    response_model=ComplianceDocumentListResponse,
    summary="List the compliance corpus",
)
async def list_compliance_documents(
    scope: Annotated[TenantScope, Depends(require_compliance_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    marketplace_code: str | None = Query(default=None, max_length=64),
) -> ComplianceDocumentListResponse:
    """Documents in this workspace, with their chunk counts."""
    filters = [ComplianceDocument.workspace_id == scope.workspace_id]
    if marketplace_code:
        filters.append(ComplianceDocument.marketplace_code == marketplace_code)

    where = filters[0]
    for condition in filters[1:]:
        where = where & condition

    total = int(
        (await db.execute(select(func.count()).where(where))).scalar_one_or_none() or 0
    )
    rows = await ComplianceDocumentRepository(db, scope.tenant).list(
        limit=limit,
        offset=offset,
        order_by=ComplianceDocument.created_at.desc(),
        where=where,
    )

    # One grouped count rather than a query per document: §57 forbids N round
    # trips on the list path, and a corpus of a few hundred policies makes that
    # the slowest route in the app.
    counts: dict[UUID, int] = {}
    if rows:
        document_ids = [row.id for row in rows]
        grouped = await db.execute(
            select(ComplianceChunk.document_id, func.count())
            .where(
                ComplianceChunk.workspace_id == scope.workspace_id,
                ComplianceChunk.document_id.in_(document_ids),
                ComplianceChunk.deleted_at.is_(None),
            )
            .group_by(ComplianceChunk.document_id)
        )
        counts = {doc_id: int(count) for doc_id, count in grouped.all()}

    return ComplianceDocumentListResponse(
        documents=[
            ComplianceDocumentRef.from_record(row, chunk_count=counts.get(row.id, 0))
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/compliance/search",
    response_model=ComplianceSearchResponse,
    summary="Search the compliance corpus for rules that support a change",
)
async def search_compliance(
    scope: Annotated[TenantScope, Depends(require_compliance_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str, Query(min_length=2, max_length=512)],
    top_k: Annotated[int, Query(ge=1, le=50)] = 8,
    marketplace_code: str | None = Query(default=None, max_length=64),
    country_code: str | None = Query(default=None, max_length=8),
    document_kind: str | None = Query(default=None, max_length=64),
    rule_kind: str | None = Query(default=None, max_length=64),
    version_label: str | None = Query(default=None, max_length=64),
    category_path: str | None = Query(default=None, max_length=1024),
    effective_on: datetime | None = Query(default=None),
) -> ComplianceSearchResponse:
    """Hybrid retrieval over marketplace policy, returning citable evidence.

    The filters are pushed into SQL rather than applied afterwards, so a rule for
    a different marketplace or a not-yet-effective version is excluded from the
    candidate set instead of being retrieved and then dropped. `effective_on`
    keeps undated documents: no date means undated policy, and hiding it would
    remove a rule the reviewer needs.
    """
    metadata_filter = service.MetadataFilter(
        marketplace_code=marketplace_code,
        country_code=country_code,
        document_kind=document_kind,
        rule_kind=rule_kind,
        version_label=version_label,
        category_path=category_path,
        effective_on=effective_on,
    )

    try:
        result = await service.search(
            db, scope.tenant, query=q, metadata_filter=metadata_filter, top_k=top_k
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    note = (
        "Evidence for a recommendation, not a compliance decision. Each hit names "
        "its document and section so a citation can be checked. Signals used: "
        + ", ".join(result.signals)
        + "."
    )
    if "semantic" not in result.signals:
        note += (
            " No embedding provider is configured, so these are keyword and "
            "metadata matches only -- an exact term match, not a judgement that "
            "the rule is relevant."
        )

    return ComplianceSearchResponse.from_result(result, note=note)