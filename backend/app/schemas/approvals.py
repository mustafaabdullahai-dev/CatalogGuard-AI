"""Approval-center request and response models (Phase 18, §20).

The queue, the evidence a reviewer compares, and the answer to a decision. The
bulk shapes exist because §20 asks for "approve selected" and "approve all
low-risk" as first-class actions, not as a UI loop over single decisions — but
each item in a bulk result reports its own outcome, so one refused proposal
cannot be mistaken for a success.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.reconciliation import ChangeProposalRef


class ApprovalQueueItem(BaseModel):
    """One proposal waiting for a human."""

    proposal: ChangeProposalRef
    # Present once a decision has been opened against it; absent otherwise,
    # which is what "no approver assigned yet" (D-020) looks like on the wire.
    required_approvals: int | None = None
    approvals_received: int | None = None
    request_status: str | None = None
    simulated: bool = False


class ApprovalQueueResponse(BaseModel):
    items: list[ApprovalQueueItem]
    total: int
    limit: int
    offset: int


class RiskSummary(BaseModel):
    """The score and the factors that produced it (§16, §39)."""

    risk_level: str
    numeric_score: float
    factors: list[Any]
    escalated_reason: str | None = None


class SimulationSummary(BaseModel):
    """What the sandbox predicted, as stored (§18, §19)."""

    simulation_id: UUID
    completed_at: datetime | None
    base_catalog_version: int
    status: str
    schema_valid: bool | None
    relationship_valid: bool | None
    compliance_valid: bool | None
    seo_sensitive: bool | None
    overall_risk: str | None
    diff: dict[str, Any]
    impact_report: dict[str, Any]
    explanation: str | None


class ApprovalRequestRef(BaseModel):
    id: UUID
    status: str
    required_approvals: int
    approvals_received: int
    decided_at: datetime | None


class ApprovalActionRef(BaseModel):
    """One person's decision, kept rather than folded into the counter."""

    decision: str
    decided_by: UUID
    notes: str | None
    modified_value: Any | None
    decided_at: datetime


class ConflictEvidence(BaseModel):
    """A disagreement about this product, with every value that produced it."""

    conflict_id: UUID
    conflict_type: str
    field_name: str
    competing_values: dict[str, Any]
    confidence: float | None


class ApprovalDetailResponse(BaseModel):
    """Everything §20's "compare evidence" needs in one response."""

    proposal: ChangeProposalRef
    approvable: bool
    approvable_reason: str | None
    risk: RiskSummary | None
    simulation: SimulationSummary | None
    request: ApprovalRequestRef | None
    actions: list[ApprovalActionRef]
    conflicts: list[ConflictEvidence]


class DecisionRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=4000)


class RejectRequest(BaseModel):
    notes: str = Field(min_length=1, max_length=4000)


class ModifyRequest(BaseModel):
    proposed_value: Any
    notes: str = Field(min_length=1, max_length=4000)


class ReAnalysisRequest(BaseModel):
    notes: str = Field(min_length=1, max_length=4000)


class DecisionResponse(BaseModel):
    proposal_id: UUID
    change_ref: str
    decision: str
    proposal_status: str
    validation_status: str
    request_status: str
    approvals_received: int
    required_approvals: int
    complete: bool
    audit_id: UUID
    detail: str


class BulkDecisionRequest(BaseModel):
    """§20's "approve selected" and "reject selected"."""

    action: Literal["approve", "reject"]
    proposal_ids: list[UUID] = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)


class BulkItemResult(BaseModel):
    proposal_id: UUID
    change_ref: str | None
    outcome: Literal["done", "refused"]
    detail: str
    proposal_status: str | None = None
    approvals_received: int | None = None
    required_approvals: int | None = None
    audit_id: UUID | None = None


class BulkDecisionResponse(BaseModel):
    results: list[BulkItemResult]
    done: int
    refused: int


class LowRiskResponse(BaseModel):
    """§20's "approve all low-risk", same per-item honesty as any bulk call."""

    results: list[BulkItemResult]
    done: int
    refused: int
    considered: int
