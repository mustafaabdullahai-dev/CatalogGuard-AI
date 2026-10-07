"""Approval workflow (Phase 18, §15, §20, §38, §39).

Authority proposes, the sandbox predicts, and this module is where a person
decides. Two rules from the design are enforced here rather than described:

- **D-020:** the risk level decides *how many* approvers, never *whether* one
  exists. Every proposal already carries `requires_approval=True`; what varies
  is `required_approvals`, and §38's ladder supplies it: LOW and MEDIUM need
  one, HIGH needs one explicit human, CRITICAL needs a second one.
- **D-003 / D-004:** the data integration admin holds credential custody and
  must never approve. That is the capability matrix's job upstream, but a
  second rule holds here — one actor records one decision per request, so a
  single person cannot supply both approvals a CRITICAL change needs.

Every decision writes an `ApprovalAction` (who, what, when, why) and an
`AuditLog` (§39's full answer, hashed). Neither is a courtesy: the request's
counter is derived from the actions, and the audit row is the record a reviewer
reads after the fact.

Nothing here reaches a marketplace. Approving moves a proposal to `APPROVED`
and stops; execution is Phase 20's separate path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import TenantContext, utcnow
from app.models.reconciliation import (
    ApprovalAction,
    ApprovalDecision,
    ApprovalRequest,
    AuditLog,
    ChangeProposal,
    ProposalStatus,
    RiskLevel,
)
from app.services.audit import audit_log_for
from app.services.reconciliation.repositories import (
    ApprovalActionRepository,
    ApprovalRequestRepository,
    AuditLogRepository,
    ChangeProposalRepository,
)

# §38's ladder as counts. LOW and MEDIUM are one approval, HIGH is one explicit
# human (already distinct from "no approval needed"), CRITICAL is multi-step.
# D-020 sits underneath all four: the level picks the number, never zero.
REQUIRED_APPROVALS: dict[RiskLevel, int] = {
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 1,
    RiskLevel.CRITICAL: 2,
}

# What the approval queue is made of: not yet decided, and either awaiting its
# sandbox or already through it.
QUEUE_STATUSES: tuple[ProposalStatus, ...] = (
    ProposalStatus.PENDING_APPROVAL,
    ProposalStatus.SIMULATED,
)

# §39 asks "why?" of every change. A rejection, an edit and a request to look
# again all need an answer; an approval can stand on the evidence already in
# the row.
_DECISIONS_REQUIRING_NOTES: frozenset[ApprovalDecision] = frozenset(
    {
        ApprovalDecision.REJECTED,
        ApprovalDecision.MODIFIED,
        ApprovalDecision.RE_ANALYSIS_REQUESTED,
    }
)


class ApprovalError(Exception):
    """A decision the workflow refuses, in words a route can return."""


class NotApprovableError(ApprovalError):
    """The proposal is not in a state where a human may decide it."""


class AlreadyDecidedError(ApprovalError):
    """This actor has already recorded a decision on this request."""


@dataclass(slots=True)
class DecisionOutcome:
    """What one decision changed, for the response and for tests."""

    request: ApprovalRequest
    action: ApprovalAction
    proposal: ChangeProposal
    audit: AuditLog
    approvals_received: int
    required_approvals: int
    complete: bool


def required_approvals_for(risk: RiskLevel) -> int:
    """How many distinct humans this risk level needs.

    An unknown level is not a free pass: it falls to one approval, the same
    floor every other level gets.
    """
    return REQUIRED_APPROVALS.get(risk, 1)


def approvable_reason(proposal: ChangeProposal) -> str | None:
    """`None` if a human may decide now; otherwise why they may not.

    The sandbox gate is deliberate: an approval carries the evidence §18
    produced, so a proposal the sandbox rejected, or has not run against, has
    nothing for a reviewer to weigh.
    """
    if proposal.validation_status != "validated":
        if proposal.validation_status == "rejected":
            return "the sandbox rejected this proposal; it cannot be approved"
        return (
            f"the sandbox has not validated this proposal "
            f"(validation_status={proposal.validation_status})"
        )
    if proposal.status not in QUEUE_STATUSES:
        return f"this proposal is already {proposal.status}"
    return None


async def open_request(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    proposal: ChangeProposal,
) -> ApprovalRequest:
    """The proposal's open request, creating one if none is pending.

    A proposal that comes back from re-analysis or a modification gets a fresh
    counter: approvals recorded against the value it used to have say nothing
    about the value it has now. The old request and its actions stay, which is
    the point of keeping them rather than editing in place.
    """
    repository = ApprovalRequestRepository(db, tenant)
    existing = await repository.list(
        limit=1,
        order_by=ApprovalRequest.created_at.desc(),
        where=and_(
            ApprovalRequest.change_proposal_id == proposal.id,
            ApprovalRequest.status == "pending",
        ),
    )
    if existing:
        return existing[0]
    return await repository.add(
        ApprovalRequest(
            change_proposal_id=proposal.id,
            status="pending",
            required_approvals=required_approvals_for(proposal.risk_level),
            approvals_received=0,
        )
    )


async def actions_for(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    request_id: UUID,
) -> list[ApprovalAction]:
    rows = await ApprovalActionRepository(db, tenant).list(
        limit=100,
        order_by=ApprovalAction.decided_at,
        where=ApprovalAction.approval_request_id == request_id,
    )
    return list(rows)


async def open_request_for(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    proposal_id: UUID,
) -> ApprovalRequest | None:
    rows = await ApprovalRequestRepository(db, tenant).list(
        limit=1,
        order_by=ApprovalRequest.created_at.desc(),
        where=and_(
            ApprovalRequest.change_proposal_id == proposal_id,
            ApprovalRequest.status == "pending",
        ),
    )
    return rows[0] if rows else None


async def requests_for(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    proposal_ids: Sequence[UUID],
) -> list[ApprovalRequest]:
    """Every request belonging to these proposals, in one query.

    The queue renders counts per proposal; N proposals must not mean N queries.
    """
    if not proposal_ids:
        return []
    rows = await ApprovalRequestRepository(db, tenant).list(
        limit=max(len(proposal_ids) * 4, 100),
        where=ApprovalRequest.change_proposal_id.in_(proposal_ids),
    )
    return list(rows)


async def record_decision(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    proposal: ChangeProposal,
    decision: ApprovalDecision,
    actor_id: UUID,
    actor_email: str | None = None,
    notes: str | None = None,
    modified_value: Any | None = None,
    now: datetime | None = None,
) -> DecisionOutcome:
    """Record one human's decision and everything that follows from it.

    The order matters: the gate is checked before a row exists, the actor's
    previous decisions are checked before this one counts, and the audit row is
    written after the state it describes — so an audit row always has a real
    action behind it, and a refused decision leaves nothing behind.
    """
    reason = approvable_reason(proposal)
    if reason is not None:
        raise NotApprovableError(reason)
    if decision in _DECISIONS_REQUIRING_NOTES and not (notes or "").strip():
        raise ApprovalError(f"{decision.value} requires a reason (§39)")
    if decision is ApprovalDecision.MODIFIED and modified_value is None:
        raise ApprovalError("a modified approval carries the edited value")

    moment = now or utcnow()
    request = await open_request(db, tenant, proposal=proposal)
    prior = await actions_for(db, tenant, request_id=request.id)
    if any(action.decided_by == actor_id for action in prior):
        raise AlreadyDecidedError(
            "this actor has already recorded a decision on this request; "
            "a second approval must come from a second person"
        )
    if request.status != "pending":
        raise NotApprovableError(f"this request is already {request.status}")

    before_status = proposal.status
    before_value = proposal.proposed_value

    if decision is ApprovalDecision.APPROVED:
        received = request.approvals_received + 1
        if received > request.required_approvals:
            raise NotApprovableError(
                f"only {request.required_approvals} approval(s) are required "
                f"and they are already recorded"
            )
        request.approvals_received = received
        if received >= request.required_approvals:
            request.status = "approved"
            request.decided_at = moment
            proposal.status = ProposalStatus.APPROVED
        else:
            # Still waiting on the second approver the level asked for.
            proposal.status = ProposalStatus.PENDING_APPROVAL
    elif decision is ApprovalDecision.REJECTED:
        request.status = "rejected"
        request.decided_at = moment
        proposal.status = ProposalStatus.REJECTED
    elif decision is ApprovalDecision.MODIFIED:
        request.status = "modified"
        request.decided_at = moment
        proposal.proposed_value = modified_value
        proposal.validation_status = "pending"
        proposal.validation_errors = []
        proposal.status = ProposalStatus.PENDING_APPROVAL
    elif decision is ApprovalDecision.RE_ANALYSIS_REQUESTED:
        request.status = "re_analysis_requested"
        request.decided_at = moment
        proposal.validation_status = "pending"
        proposal.validation_errors = []
        proposal.status = ProposalStatus.PENDING_APPROVAL
    else:  # pragma: no cover - the enum has four members, all handled above
        raise ApprovalError(f"unknown decision: {decision}")

    action = ApprovalAction(
        approval_request_id=request.id,
        decided_by=actor_id,
        decision=decision,
        notes=notes,
        modified_value=modified_value,
        decided_at=moment,
    )
    await ApprovalActionRepository(db, tenant).add(action)
    # `add` on a row already in the session is how a mutation is flushed here,
    # which is the pattern `persist_simulation` established in Phase 17.
    await ChangeProposalRepository(db, tenant).add(proposal)
    await ApprovalRequestRepository(db, tenant).add(request)

    audit = audit_log_for(
        workspace_id=tenant.workspace_id,
        actor_id=actor_id,
        actor_email=actor_email,
        action=f"approval.{decision.value}",
        entity_type="change_proposal",
        entity_id=proposal.id,
        created_at=moment,
        old_value={
            "status": str(before_status),
            "proposed_value": before_value,
            "approvals_received": request.approvals_received - 1
            if decision is ApprovalDecision.APPROVED
            else request.approvals_received,
        },
        new_value={
            "status": str(proposal.status),
            "proposed_value": proposal.proposed_value,
            "approvals_received": request.approvals_received,
            "request_status": request.status,
        },
        reason=notes,
        evidence=list(proposal.evidence or []),
        risk_level=proposal.risk_level,
        approval_reference=request.id,
    )
    await AuditLogRepository(db, tenant).add(audit)

    return DecisionOutcome(
        request=request,
        action=action,
        proposal=proposal,
        audit=audit,
        approvals_received=request.approvals_received,
        required_approvals=request.required_approvals,
        complete=request.status in {"approved", "rejected"},
    )
