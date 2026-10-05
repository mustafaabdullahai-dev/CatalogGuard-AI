"""Reconciliation endpoints (Phase 12, §11, §12, §15, §16).

One mutating route that runs a pass over a catalog, and three read routes for
what a pass produced: the conflicts it recorded, the proposals awaiting a human,
and the runs themselves.

Nothing here applies a change. The run records disagreement and proposes; the
approval and execution paths are separate routers (Phases 18 and 20), so a bug in
this one cannot reach a live marketplace listing.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantScope, require_reconciliation_authority
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.reconciliation import (
    ChangeProposal,
    Conflict,
    ReconciliationRun,
)
from app.schemas.reconciliation import (
    ChangeProposalListResponse,
    ChangeProposalRef,
    ConflictListResponse,
    ConflictRef,
    ReconciliationResponse,
    ReconciliationRunListResponse,
    ReconciliationRunRef,
)
from app.services.catalog.repositories import CatalogRepository
from app.services.reconciliation import engine
from app.services.reconciliation.repositories import (
    ChangeProposalRepository,
    ConflictRepository,
    ReconciliationRunRepository,
)

router = APIRouter(tags=["reconciliation"])
logger = get_logger(__name__)


@router.post(
    "/catalogs/{catalog_id}/reconcile",
    response_model=ReconciliationResponse,
    summary="Run a reconciliation pass over a catalog",
)
async def reconcile(
    catalog_id: UUID,
    scope: Annotated[TenantScope, Depends(require_reconciliation_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=5000)] = engine.DEFAULT_PRODUCT_LIMIT,
) -> ReconciliationResponse:
    """Detect conflicts, score proposals, and record both against a run.

    Every proposal it writes is `PENDING_APPROVAL`. This route never mutates a
    product's canonical value — that is the approval and execution path.
    """
    catalog = await CatalogRepository(db, scope.tenant).get(catalog_id)
    if catalog is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Catalog not found"
        )

    summary = await engine.reconcile_catalog(
        db, scope.tenant, catalog=catalog, limit=limit
    )
    await db.commit()

    return ReconciliationResponse.from_summary(
        summary,
        note=(
            "Conflicts record disagreement; proposals carry a deterministically "
            "risk-scored value and all remain PENDING_APPROVAL. No product value "
            "was changed."
        ),
    )


@router.get(
    "/conflicts",
    response_model=ConflictListResponse,
    summary="List recorded disagreements",
)
async def list_conflicts(
    scope: Annotated[TenantScope, Depends(require_reconciliation_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    product_id: UUID | None = Query(default=None),
    open_only: bool = Query(default=True),
) -> ConflictListResponse:
    """Every competing value, retained.

    `open_only` defaults to true because a resolved conflict is history: it stays
    in the table for the audit trail but out of the queue a reviewer works
    through.
    """
    filters = [Conflict.workspace_id == scope.workspace_id]
    if product_id is not None:
        filters.append(Conflict.product_id == product_id)
    if open_only:
        filters.append(Conflict.resolved_at.is_(None))

    where = filters[0]
    for condition in filters[1:]:
        where = where & condition

    total = int(
        (await db.execute(select(func.count()).where(where))).scalar_one_or_none() or 0
    )
    rows = await ConflictRepository(db, scope.tenant).list(
        limit=limit, offset=offset, order_by=Conflict.created_at, where=where
    )
    return ConflictListResponse(
        conflicts=[ConflictRef.from_record(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/change-proposals",
    response_model=ChangeProposalListResponse,
    summary="List proposals awaiting a decision",
)
async def list_change_proposals(
    scope: Annotated[TenantScope, Depends(require_reconciliation_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    risk_level: str | None = Query(default=None, max_length=16),
) -> ChangeProposalListResponse:
    """Proposals, risk level descending so the urgent ones surface first.

    `confidence` is returned but never sorted on: it is evidence for a reviewer,
    not a ranking of how much a change deserves to happen (§37).
    """
    filters = [ChangeProposal.workspace_id == scope.workspace_id]
    if risk_level:
        filters.append(ChangeProposal.risk_level == risk_level)
    where = filters[0]
    for condition in filters[1:]:
        where = where & condition

    total = int(
        (await db.execute(select(func.count()).where(where))).scalar_one_or_none() or 0
    )
    rows = await ChangeProposalRepository(db, scope.tenant).list(
        limit=limit,
        offset=offset,
        order_by=ChangeProposal.created_at,
        where=where,
    )
    return ChangeProposalListResponse(
        proposals=[ChangeProposalRef.from_record(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/reconciliation-runs",
    response_model=ReconciliationRunListResponse,
    summary="List past reconciliation runs",
)
async def list_reconciliation_runs(
    scope: Annotated[TenantScope, Depends(require_reconciliation_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReconciliationRunListResponse:
    where = ReconciliationRun.workspace_id == scope.workspace_id
    total = int(
        (await db.execute(select(func.count()).where(where))).scalar_one_or_none() or 0
    )
    rows = await ReconciliationRunRepository(db, scope.tenant).list(
        limit=limit, offset=offset, order_by=ReconciliationRun.started_at, where=where
    )
    return ReconciliationRunListResponse(
        runs=[ReconciliationRunRef.from_record(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )

