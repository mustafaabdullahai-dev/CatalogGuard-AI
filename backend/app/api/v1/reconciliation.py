"""Reconciliation endpoints (Phase 12, §11, §12, §15, §16; Phase 17, §18).

Two mutating routes: one runs a pass over a catalog, the other runs one
proposal through the sandbox and records what it predicted. The read routes
report what a pass produced — the conflicts it recorded, the proposals awaiting
a human, the runs themselves.

Nothing here applies a change. The run records disagreement and proposes; the
simulation predicts and stores a report; the approval and execution paths are
separate routers (Phases 18 and 20), so a bug in this one cannot reach a live
marketplace listing.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantScope, require_reconciliation_authority
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.catalog import Product, ProductRelationship
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
    SimulationResponse,
)
from app.services.catalog.relationships import (
    VARIATION_AXES,
    RelationshipEdge,
    TreeNode,
)
from app.services.catalog.repositories import CatalogRepository, ProductRepository
from app.services.reconciliation import engine
from app.services.reconciliation.repositories import (
    ChangeProposalRepository,
    ConflictRepository,
    ReconciliationRunRepository,
)
from app.services.simulation import (
    CatalogSnapshot,
    ProductState,
    ProposedChange,
    persist_simulation,
    simulate,
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


async def _snapshot_for(
    db: AsyncSession,
    scope: TenantScope,
    *,
    product: Product,
    catalog_version: int,
) -> CatalogSnapshot:
    """Load enough of the tree to validate one proposal's move.

    Not the whole catalog: a simulation answers "what does *this* change do",
    and a field write can only disturb the product, its parent, its children
    and their siblings. What the neighborhood does not contain is reported as
    it was found, not guessed at — the next reconciliation pass reads the rest.
    """
    related = {product.id}
    if product.parent_id is not None:
        related.add(product.parent_id)
    neighbors = await ProductRepository(db, scope.tenant).list(
        limit=1000,
        where=or_(Product.id.in_(related), Product.parent_id.in_(related)),
    )
    # A proposal's product may be the only row the query found (a brand-new
    # catalog), and it must still appear in the tree it is being moved inside.
    if not any(neighbor.id == product.id for neighbor in neighbors):
        neighbors = [product, *neighbors]

    nodes = tuple(
        TreeNode(
            id=neighbor.id,
            sku=neighbor.sku,
            title=neighbor.title,
            is_parent=bool(neighbor.is_parent),
            parent_id=neighbor.parent_id,
            variation_attributes={
                axis: neighbor.attributes[axis]
                for axis in VARIATION_AXES
                if neighbor.attributes.get(axis) not in (None, "")
            },
        )
        for neighbor in neighbors
    )
    neighbor_ids = {neighbor.id for neighbor in neighbors}
    rows = await db.execute(
        select(ProductRelationship).where(
            ProductRelationship.workspace_id == scope.workspace_id,
            or_(
                ProductRelationship.parent_id.in_(neighbor_ids),
                ProductRelationship.child_id.in_(neighbor_ids),
            ),
        )
    )
    edges = tuple(
        RelationshipEdge(
            parent_id=relationship.parent_id,
            child_id=relationship.child_id,
            relationship_type=str(relationship.relationship_type),
            established_by=relationship.established_by,
            approved=relationship.approval_reference is not None,
        )
        for relationship in rows.scalars().all()
    )
    return CatalogSnapshot(
        product=ProductState.from_product(product),
        nodes=nodes,
        edges=edges,
        catalog_version=catalog_version,
    )


@router.post(
    "/change-proposals/{proposal_id}/simulate",
    response_model=SimulationResponse,
    summary="Predict a proposal's impact without applying it (§18)",
)
async def simulate_change_proposal(
    proposal_id: UUID,
    scope: Annotated[TenantScope, Depends(require_reconciliation_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> SimulationResponse:
    """Run §18's chain over a copy of the product and store what it predicted.

    The product row is never written: the outcome lands in a `SimulationRun`,
    and the proposal moves to `simulated` or `rejected_by_validation`. A
    proposal whose product or catalog is gone has no row to copy, so it is
    refused rather than simulated against a snapshot assembled from nothing.
    """
    proposal = await ChangeProposalRepository(db, scope.tenant).get(proposal_id)
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="proposal not found"
        )
    if proposal.product_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a proposal with no product has nothing to simulate",
        )
    product = await ProductRepository(db, scope.tenant).get(proposal.product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="the product this proposal targets no longer exists",
        )
    catalog = await CatalogRepository(db, scope.tenant).get(product.catalog_id)
    if catalog is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="the catalog this product belongs to no longer exists",
        )

    snapshot = await _snapshot_for(
        db, scope, product=product, catalog_version=catalog.current_version
    )
    result = await simulate(ProposedChange.from_proposal(proposal), snapshot)
    run = await persist_simulation(db, scope.tenant, proposal=proposal, result=result)
    logger.info(
        "simulation recorded",
        proposal_id=str(proposal.id),
        simulation_id=str(run.id),
        validation_status=result.validation_status,
        overall_risk=str(result.overall_risk),
    )
    return SimulationResponse.from_result(
        simulation_id=run.id, proposal=proposal, result=result
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

