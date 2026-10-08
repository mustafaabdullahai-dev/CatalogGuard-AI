"""Run §18's chain for one proposal (Phase 17, extended by Phase 22).

Extracted from the `POST /change-proposals/{id}/simulate` route so that the
copilot's `sandbox_simulator` tool and the HTTP route run *the same* simulation
rather than two implementations that drift. The route maps refusals to status
codes; the tool maps them to answer data. Neither decides anything: the chain is
deterministic, the product row is never written, and the outcome lands in a
`SimulationRun`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import TenantContext
from app.models.catalog import Product, ProductRelationship
from app.models.reconciliation import ChangeProposal
from app.services.catalog.relationships import (
    VARIATION_AXES,
    RelationshipEdge,
    TreeNode,
)
from app.services.catalog.repositories import (
    CatalogRepository,
    ProductRepository,
)
from app.services.reconciliation.repositories import ChangeProposalRepository
from app.services.simulation.persistence import persist_simulation
from app.services.simulation.sandbox import (
    CatalogSnapshot,
    ProductState,
    ProposedChange,
    SimulationResult,
    simulate,
)

RefusalReason = Literal["proposal_not_found", "no_product", "no_catalog"]


class SimulationRefusedError(RuntimeError):
    """A proposal that cannot be simulated, and why.

    Not an HTTP status: the same refusal is a 404 from the route and a sentence
    the copilot reads back to a person.
    """

    def __init__(self, reason: RefusalReason) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SimulationOutcome:
    proposal: ChangeProposal
    simulation_id: UUID
    result: SimulationResult


async def simulate_proposal(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    proposal_id: UUID,
) -> SimulationOutcome:
    """Simulate one proposal and store what it predicted.

    A proposal whose product or catalog is gone has no row to copy, so it is
    refused rather than simulated against a snapshot assembled from nothing —
    a prediction about a product that no longer exists is not a weaker answer,
    it is a wrong one.
    """
    proposal = await ChangeProposalRepository(session, tenant).get(proposal_id)
    if proposal is None:
        raise SimulationRefusedError("proposal_not_found")
    if proposal.product_id is None:
        raise SimulationRefusedError("no_product")

    product = await ProductRepository(session, tenant).get(proposal.product_id)
    if product is None:
        raise SimulationRefusedError("no_product")
    catalog = await CatalogRepository(session, tenant).get(product.catalog_id)
    if catalog is None:
        raise SimulationRefusedError("no_catalog")

    snapshot = await _snapshot_for(
        session, tenant, product=product, catalog_version=catalog.current_version
    )
    result = await simulate(ProposedChange.from_proposal(proposal), snapshot)
    run = await persist_simulation(session, tenant, proposal=proposal, result=result)
    return SimulationOutcome(proposal=proposal, simulation_id=run.id, result=result)


async def _snapshot_for(
    session: AsyncSession,
    tenant: TenantContext,
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
    neighbors = await ProductRepository(session, tenant).list(
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
    rows = await session.execute(
        select(ProductRelationship).where(
            ProductRelationship.workspace_id == tenant.workspace_id,
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