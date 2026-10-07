"""Writing what the sandbox produced (Phase 17, §18).

Two rules, both from the phase's exit criteria:

- The run is stored (`simulation_runs`), with its flags, its diff and its
  report, against the catalog version it read — so a result can be replayed
  and argued with later rather than trusted from memory.
- `validation_status` moves off `pending` *because a run moved it*. Nothing
  in the engine writes `validated`; a proposal that has never been simulated
  keeps saying `pending`, which is the honest state for it.

The proposal update is a mutation of a loaded row rather than a new insert,
and `add` on an already-persistent object is how the repository flushes a
change (it stamps the tenant and flushes). Both writers run inside the
caller's transaction, so a run that fails takes its proposal back with it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import TenantContext
from app.models.reconciliation import ChangeProposal, SimulationRun
from app.services.reconciliation.repositories import (
    ChangeProposalRepository,
    SimulationRunRepository,
)
from app.services.simulation.sandbox import SimulationResult


def simulation_run_for(proposal_id: UUID, result: SimulationResult) -> SimulationRun:
    """The `simulation_runs` row for one completed run.

    Pure, and therefore testable without a database — the same pairing that
    `risk_assessment_for` gives the risk engine (Phase 16).
    """
    return SimulationRun(
        change_proposal_id=proposal_id,
        base_catalog_version=result.base_catalog_version,
        status="completed",
        schema_valid=result.schema_valid,
        relationship_valid=result.relationship_valid,
        compliance_valid=result.compliance_valid,
        seo_sensitive=result.seo_sensitive,
        overall_risk=result.overall_risk,
        diff=result.diff,
        impact_report=result.impact_report,
        explanation=result.explanation,
        completed_at=datetime.now(UTC),
    )


async def persist_simulation(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    proposal: ChangeProposal,
    result: SimulationResult,
) -> SimulationRun:
    """Store the run and move the proposal to whatever the run decided."""
    run = await SimulationRunRepository(db, tenant).add(
        simulation_run_for(proposal.id, result)
    )

    proposal.validation_status = result.validation_status
    proposal.validation_errors = list(result.validation_errors)
    proposal.status = result.proposal_status
    await ChangeProposalRepository(db, tenant).add(proposal)
    return run
