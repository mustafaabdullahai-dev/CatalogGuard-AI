"""Approval-center endpoints (Phase 18, §20, §38, §39).

The queue, the evidence behind one proposal, and the four decisions §20 asks
for — approve, reject, modify, request re-analysis — plus the two bulk forms
and "approve all low-risk". Every route sits behind `require_approval_authority`,
which resolves to the `approve` capability: the data integration admin holds
credential custody and does not hold it (D-003, D-004), so that role is refused
here by the capability matrix rather than by a check written in this file.

Nothing executes. A decision moves a proposal and writes an audit row; reaching
a marketplace is Phase 20's separate path with its own gate.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantScope, require_approval_authority
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.reconciliation import (
    ApprovalDecision,
    ChangeProposal,
    Conflict,
    ProposalStatus,
    RiskAssessment,
    RiskLevel,
    SimulationRun,
)
from app.schemas.approvals import (
    ApprovalActionRef,
    ApprovalDetailResponse,
    ApprovalQueueItem,
    ApprovalQueueResponse,
    ApprovalRequestRef,
    BulkDecisionRequest,
    BulkDecisionResponse,
    BulkItemResult,
    ConflictEvidence,
    DecisionRequest,
    DecisionResponse,
    LowRiskResponse,
    ModifyRequest,
    ReAnalysisRequest,
    RejectRequest,
    RiskSummary,
    SimulationSummary,
)
from app.schemas.reconciliation import ChangeProposalRef
from app.services.approval import (
    ApprovalError,
    actions_for,
    approvable_reason,
    record_decision,
    requests_for,
)
from app.services.reconciliation.repositories import (
    ChangeProposalRepository,
    ConflictRepository,
    RiskAssessmentRepository,
    SimulationRunRepository,
)

router = APIRouter(prefix="/approvals", tags=["approvals"])
logger = get_logger(__name__)


async def _proposal_or_404(
    db: AsyncSession, scope: TenantScope, proposal_id: UUID
) -> ChangeProposal:
    proposal = await ChangeProposalRepository(db, scope.tenant).get(proposal_id)
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="proposal not found"
        )
    return proposal


def _refuse(error: ApprovalError) -> HTTPException:
    """A refused decision is a conflict about state, not a server fault."""
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))


def _decision_detail(
    decision: str, *, received: int, required: int, complete: bool
) -> str:
    if decision == "approved":
        if complete:
            return "all required approvals recorded; the proposal is approved"
        return (
            f"{received} of {required} approvals recorded; "
            f"a second approver is still required"
        )
    if decision == "rejected":
        return "rejected; this proposal will not be executed"
    if decision == "modified":
        return "modified; the new value must be simulated again before approval"
    return "re-analysis requested; the sandbox must run against this proposal again"


def _ok(response: DecisionResponse) -> BulkItemResult:
    return BulkItemResult(
        proposal_id=response.proposal_id,
        change_ref=response.change_ref,
        outcome="done",
        detail=response.detail,
        proposal_status=response.proposal_status,
        approvals_received=response.approvals_received,
        required_approvals=response.required_approvals,
        audit_id=response.audit_id,
    )


async def _decide(
    db: AsyncSession,
    scope: TenantScope,
    *,
    proposal_id: UUID,
    decision: ApprovalDecision,
    notes: str | None,
    modified_value: Any = None,
) -> DecisionResponse:
    proposal = await _proposal_or_404(db, scope, proposal_id)
    try:
        outcome = await record_decision(
            db,
            scope.tenant,
            proposal=proposal,
            decision=decision,
            actor_id=scope.context.user.id,
            actor_email=scope.context.user.email,
            notes=notes,
            modified_value=modified_value,
        )
    except ApprovalError as error:
        raise _refuse(error) from error
    logger.info(
        "approval decision recorded",
        proposal_id=str(proposal.id),
        decision=decision.value,
        approvals_received=outcome.approvals_received,
        required_approvals=outcome.required_approvals,
    )
    return DecisionResponse(
        proposal_id=proposal.id,
        change_ref=proposal.change_ref,
        decision=decision.value,
        proposal_status=str(proposal.status),
        validation_status=proposal.validation_status,
        request_status=outcome.request.status,
        approvals_received=outcome.approvals_received,
        required_approvals=outcome.required_approvals,
        complete=outcome.complete,
        audit_id=outcome.audit.id,
        detail=_decision_detail(
            decision.value,
            received=outcome.approvals_received,
            required=outcome.required_approvals,
            complete=outcome.complete,
        ),
    )


async def _attempt(
    db: AsyncSession,
    scope: TenantScope,
    *,
    proposal_id: UUID,
    decision: ApprovalDecision,
    notes: str | None,
) -> BulkItemResult:
    """One proposal, one decision, one honest answer — never a raised batch."""
    try:
        response = await _decide(
            db, scope, proposal_id=proposal_id, decision=decision, notes=notes
        )
    except HTTPException as error:
        proposal = await ChangeProposalRepository(db, scope.tenant).get(proposal_id)
        return BulkItemResult(
            proposal_id=proposal_id,
            change_ref=proposal.change_ref if proposal is not None else None,
            outcome="refused",
            detail=str(error.detail),
            proposal_status=str(proposal.status) if proposal is not None else None,
        )
    return _ok(response)


@router.get(
    "",
    response_model=ApprovalQueueResponse,
    summary="List proposals awaiting a decision",
)
async def list_approvals(
    scope: Annotated[TenantScope, Depends(require_approval_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    risk_level: Annotated[str | None, Query(max_length=16)] = None,
) -> ApprovalQueueResponse:
    """The queue §20 opens on: everything not yet decided.

    `simulated` sits beside each proposal so a caller can tell one the sandbox
    has validated from one still waiting for it. The second kind cannot be
    approved, and the UI should be able to say why before someone clicks.
    """
    where: Any = or_(
        ChangeProposal.status == ProposalStatus.PENDING_APPROVAL,
        ChangeProposal.status == ProposalStatus.SIMULATED,
    )
    if risk_level:
        where = where & (ChangeProposal.risk_level == risk_level)

    total = int(
        (await db.execute(select(func.count()).where(where))).scalar_one_or_none() or 0
    )
    rows = await ChangeProposalRepository(db, scope.tenant).list(
        limit=limit,
        offset=offset,
        order_by=ChangeProposal.created_at,
        where=where,
    )
    proposal_ids = [row.id for row in rows]
    requests = await requests_for(db, scope.tenant, proposal_ids=proposal_ids)
    simulations = await SimulationRunRepository(db, scope.tenant).list(
        limit=max(len(proposal_ids), 1),
        order_by=SimulationRun.created_at.desc(),
        where=(
            SimulationRun.change_proposal_id.in_(proposal_ids)
            if proposal_ids
            else False
        ),
    )
    by_proposal: dict[UUID, Any] = {}
    for request in requests:
        # An open request wins; otherwise the most recent one still shows the
        # count that decision left behind.
        current = by_proposal.get(request.change_proposal_id)
        if current is None or request.status == "pending":
            by_proposal[request.change_proposal_id] = request
    simulated = {row.change_proposal_id for row in simulations}

    return ApprovalQueueResponse(
        items=[
            ApprovalQueueItem(
                proposal=ChangeProposalRef.from_record(row),
                required_approvals=(
                    by_proposal[row.id].required_approvals
                    if row.id in by_proposal
                    else None
                ),
                approvals_received=(
                    by_proposal[row.id].approvals_received
                    if row.id in by_proposal
                    else None
                ),
                request_status=(
                    by_proposal[row.id].status if row.id in by_proposal else None
                ),
                simulated=row.id in simulated,
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{proposal_id}",
    response_model=ApprovalDetailResponse,
    summary="Compare the evidence behind one proposal",
)
async def approval_detail(
    proposal_id: UUID,
    scope: Annotated[TenantScope, Depends(require_approval_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ApprovalDetailResponse:
    """§20's "compare evidence": the proposal, its score, the sandbox's
    prediction, every competing value the conflict kept, and who has already
    decided. Nothing is summarized away — a reviewer comparing sources needs
    the values themselves.
    """
    proposal = await _proposal_or_404(db, scope, proposal_id)

    risk_rows = await RiskAssessmentRepository(db, scope.tenant).list(
        limit=1,
        order_by=RiskAssessment.created_at.desc(),
        where=RiskAssessment.change_proposal_id == proposal.id,
    )
    simulation_rows = await SimulationRunRepository(db, scope.tenant).list(
        limit=1,
        order_by=SimulationRun.created_at.desc(),
        where=SimulationRun.change_proposal_id == proposal.id,
    )
    request_rows = await requests_for(db, scope.tenant, proposal_ids=[proposal.id])
    open_row = next((row for row in request_rows if row.status == "pending"), None)
    current = open_row or (request_rows[0] if request_rows else None)
    actions = (
        await actions_for(db, scope.tenant, request_id=current.id)
        if current is not None
        else []
    )

    conflicts: list[Conflict] = []
    if proposal.product_id is not None:
        conflicts = list(
            await ConflictRepository(db, scope.tenant).list(
                limit=20,
                order_by=Conflict.created_at.desc(),
                where=Conflict.product_id == proposal.product_id,
            )
        )

    risk = risk_rows[0] if risk_rows else None
    simulation = simulation_rows[0] if simulation_rows else None
    return ApprovalDetailResponse(
        proposal=ChangeProposalRef.from_record(proposal),
        approvable=approvable_reason(proposal) is None,
        approvable_reason=approvable_reason(proposal),
        risk=(
            RiskSummary(
                risk_level=str(risk.risk_level),
                numeric_score=risk.numeric_score,
                factors=list(risk.factors or []),
                escalated_reason=risk.escalated_reason,
            )
            if risk is not None
            else None
        ),
        simulation=(
            SimulationSummary(
                simulation_id=simulation.id,
                completed_at=simulation.completed_at,
                base_catalog_version=simulation.base_catalog_version,
                status=simulation.status,
                schema_valid=simulation.schema_valid,
                relationship_valid=simulation.relationship_valid,
                compliance_valid=simulation.compliance_valid,
                seo_sensitive=simulation.seo_sensitive,
                overall_risk=(
                    str(simulation.overall_risk)
                    if simulation.overall_risk is not None
                    else None
                ),
                diff=dict(simulation.diff or {}),
                impact_report=dict(simulation.impact_report or {}),
                explanation=simulation.explanation,
            )
            if simulation is not None
            else None
        ),
        request=(
            ApprovalRequestRef(
                id=current.id,
                status=current.status,
                required_approvals=current.required_approvals,
                approvals_received=current.approvals_received,
                decided_at=current.decided_at,
            )
            if current is not None
            else None
        ),
        actions=[
            ApprovalActionRef(
                decision=str(action.decision),
                decided_by=action.decided_by,
                notes=action.notes,
                modified_value=action.modified_value,
                decided_at=action.decided_at,
            )
            for action in actions
        ],
        conflicts=[
            ConflictEvidence(
                conflict_id=conflict.id,
                conflict_type=str(conflict.conflict_type),
                field_name=conflict.field_name,
                competing_values=dict(conflict.competing_values or {}),
                confidence=conflict.confidence,
            )
            for conflict in conflicts
        ],
    )


@router.post(
    "/{proposal_id}/approve",
    response_model=DecisionResponse,
    summary="Approve one proposal",
)
async def approve(
    proposal_id: UUID,
    scope: Annotated[TenantScope, Depends(require_approval_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    body: DecisionRequest | None = None,
) -> DecisionResponse:
    return await _decide(
        db,
        scope,
        proposal_id=proposal_id,
        decision=ApprovalDecision.APPROVED,
        notes=body.notes if body else None,
    )


@router.post(
    "/{proposal_id}/reject",
    response_model=DecisionResponse,
    summary="Reject one proposal",
)
async def reject(
    proposal_id: UUID,
    scope: Annotated[TenantScope, Depends(require_approval_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    body: RejectRequest,
) -> DecisionResponse:
    return await _decide(
        db,
        scope,
        proposal_id=proposal_id,
        decision=ApprovalDecision.REJECTED,
        notes=body.notes,
    )


@router.post(
    "/{proposal_id}/modify",
    response_model=DecisionResponse,
    summary="Approve a different value, and send it back through the sandbox",
)
async def modify(
    proposal_id: UUID,
    scope: Annotated[TenantScope, Depends(require_approval_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    body: ModifyRequest,
) -> DecisionResponse:
    return await _decide(
        db,
        scope,
        proposal_id=proposal_id,
        decision=ApprovalDecision.MODIFIED,
        notes=body.notes,
        modified_value=body.proposed_value,
    )


@router.post(
    "/{proposal_id}/re-analysis",
    response_model=DecisionResponse,
    summary="Send a proposal back for another look",
)
async def request_re_analysis(
    proposal_id: UUID,
    scope: Annotated[TenantScope, Depends(require_approval_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    body: ReAnalysisRequest,
) -> DecisionResponse:
    return await _decide(
        db,
        scope,
        proposal_id=proposal_id,
        decision=ApprovalDecision.RE_ANALYSIS_REQUESTED,
        notes=body.notes,
    )


@router.post(
    "",
    response_model=BulkDecisionResponse,
    summary="Approve or reject a selection (§20)",
)
async def bulk_decision(
    scope: Annotated[TenantScope, Depends(require_approval_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    body: BulkDecisionRequest,
) -> BulkDecisionResponse:
    """One call, many proposals, one honest result per proposal.

    A refusal is reported per item rather than aborting the batch: someone
    selecting ten proposals should learn that the one they also meant was
    already approved, not that the whole call failed and none of them moved.
    """
    if body.action == "reject" and not (body.notes or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="a rejection requires a reason (§39)",
        )
    decision = (
        ApprovalDecision.APPROVED if body.action == "approve" else ApprovalDecision.REJECTED
    )
    results = [
        await _attempt(db, scope, proposal_id=proposal_id, decision=decision, notes=body.notes)
        for proposal_id in body.proposal_ids
    ]
    done = sum(1 for result in results if result.outcome == "done")
    return BulkDecisionResponse(results=results, done=done, refused=len(results) - done)


@router.post(
    "/approve-low-risk",
    response_model=LowRiskResponse,
    summary="Approve every low-risk proposal in the queue (§20)",
)
async def approve_low_risk(
    scope: Annotated[TenantScope, Depends(require_approval_authority())],
    db: Annotated[AsyncSession, Depends(get_session)],
    notes: Annotated[str | None, Query(max_length=4000)] = None,
) -> LowRiskResponse:
    """§38's LOW band, still approved by a human — bulk, not automatic.

    D-020 rejected auto-approval explicitly: risk level decides how many
    approvers a change needs, never whether one is needed. This endpoint makes
    giving that one approval to a whole batch cheap; it does not remove the
    person.
    """
    where: Any = (
        (ChangeProposal.status == ProposalStatus.PENDING_APPROVAL)
        | (ChangeProposal.status == ProposalStatus.SIMULATED)
    ) & (ChangeProposal.risk_level == RiskLevel.LOW)
    rows = await ChangeProposalRepository(db, scope.tenant).list(
        limit=200,
        order_by=ChangeProposal.created_at,
        where=where,
    )
    results = [
        await _attempt(
            db, scope, proposal_id=proposal.id, decision=ApprovalDecision.APPROVED, notes=notes
        )
        for proposal in rows
    ]
    done = sum(1 for result in results if result.outcome == "done")
    return LowRiskResponse(
        results=results, done=done, refused=len(results) - done, considered=len(rows)
    )
