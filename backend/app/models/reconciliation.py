"""Reconciliation, risk, simulation, approval, and execution models.

These tables implement the pipeline from Section 5: a proposal is created,
deterministically validated, risk-scored, simulated, approved, executed, and
audited. Each stage is a distinct table so a failure at any stage leaves a
legible record rather than an ambiguous state.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    Base,
    SoftDeletableTenantEntity,
    TenantScopedMixin,
    UUIDPrimaryKeyMixin,
    str_enum,
)


class ConflictType(enum.StrEnum):
    ATTRIBUTE_MISMATCH = "attribute_mismatch"
    MISSING_ATTRIBUTE = "missing_attribute"
    DUPLICATE_PRODUCT = "duplicate_product"
    SKU_CONFLICT = "sku_conflict"
    VARIANT_CONFLICT = "variant_conflict"
    RELATIONSHIP_BREAK = "relationship_break"
    ORPHAN_CHILD = "orphan_child"
    COMPLIANCE_VIOLATION = "compliance_violation"
    IDENTITY_MISMATCH = "identity_mismatch"


class RiskLevel(enum.StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _RISK_RANKS[self]

    @property
    def requires_explicit_approval(self) -> bool:
        """HIGH and CRITICAL always need a human, per Section 38."""
        return self.rank >= 3


_RISK_RANKS: dict[RiskLevel, int] = {
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


class ProposalStatus(enum.StrEnum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    REJECTED_BY_VALIDATION = "rejected_by_validation"
    SIMULATED = "simulated"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ApprovalDecision(enum.StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    RE_ANALYSIS_REQUESTED = "re_analysis_requested"


class ExecutionStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIALLY_FAILED = "partially_failed"
    ROLLED_BACK = "rolled_back"


class ReconciliationRun(SoftDeletableTenantEntity):
    """One reconciliation pass over a catalog version (Section 50)."""

    __tablename__ = "reconciliation_runs"

    catalog_id: Mapped[UUID] = mapped_column(nullable=False)
    # Binds the run to the catalog state it observed, so findings replay.
    catalog_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    products_analyzed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conflicts_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Token and cost accounting, built in-house since D-010 forbids vendor dashboards.
    llm_tokens_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "catalog_id"],
            ["catalogs.workspace_id", "catalogs.id"],
            name="fk_runs_workspace_catalog",
            ondelete="CASCADE",
        ),
        Index("ix_runs_workspace_catalog_time", "workspace_id", "catalog_id", "started_at"),
        {"comment": "One reconciliation pass, bound to a catalog version"},
    )


class Conflict(SoftDeletableTenantEntity):
    """A detected disagreement between sources (Section 11)."""

    __tablename__ = "conflicts"

    product_id: Mapped[UUID | None] = mapped_column(nullable=True)
    reconciliation_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    conflict_type: Mapped[ConflictType] = mapped_column(
        str_enum(ConflictType, name="conflict_type", length=64), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Every competing value, keyed by source. Nothing is silently discarded.
    competing_values: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    resolved_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_conflicts_workspace_product",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "reconciliation_run_id"],
            ["reconciliation_runs.workspace_id", "reconciliation_runs.id"],
            name="fk_conflicts_workspace_run",
            ondelete="SET NULL",
        ),
        Index("ix_conflicts_workspace_product", "workspace_id", "product_id"),
        Index("ix_conflicts_workspace_field", "workspace_id", "field_name"),
        Index("ix_conflicts_workspace_type", "workspace_id", "conflict_type"),
        {"comment": "Detected disagreement; all competing values retained"},
    )


class ChangeProposal(SoftDeletableTenantEntity):
    """A structured, validated change request (Section 15).

    Free-form model output must never reach the execution layer. This table
    is the boundary: only rows that passed deterministic validation and risk
    assessment get here.
    """

    __tablename__ = "change_proposals"

    product_id: Mapped[UUID | None] = mapped_column(nullable=True)
    reconciliation_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    change_ref: Mapped[str] = mapped_column(String(64), nullable=False)

    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    current_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    proposed_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)

    # Section 37: confidence is not certainty, and never means "execute".
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    validation_errors: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)

    risk_level: Mapped[RiskLevel] = mapped_column(
        str_enum(RiskLevel, name="risk_level", length=16), nullable=False
    )
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False)

    seo_impact: Mapped[str] = mapped_column(String(32), default="NONE", nullable=False)
    compliance_impact: Mapped[str] = mapped_column(
        String(32), default="NEUTRAL", nullable=False
    )
    affected_channels: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    affected_relationships: Mapped[list[Any]] = mapped_column(
        JSON, default=list, nullable=False
    )

    status: Mapped[ProposalStatus] = mapped_column(
        str_enum(ProposalStatus, name="proposal_status", length=32),
        default=ProposalStatus.PROPOSED,
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_proposals_workspace_product",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "reconciliation_run_id"],
            ["reconciliation_runs.workspace_id", "reconciliation_runs.id"],
            name="fk_proposals_workspace_run",
            ondelete="SET NULL",
        ),
        UniqueConstraint("workspace_id", "change_ref", name="uq_proposals_change_ref"),
        Index("ix_proposals_workspace_status", "workspace_id", "status"),
        Index("ix_proposals_workspace_risk", "workspace_id", "risk_level"),
        # Section 37: confidence is evidence, never permission. Excluding both
        # endpoints means no code path can treat confidence as certainty, and
        # no value can be read as "execute".
        CheckConstraint(
            "confidence > 0.0 AND confidence < 1.0", name="confidence_range"
        ),
        {"comment": "Validated, risk-scored change request awaiting approval"},
    )


class RiskAssessment(SoftDeletableTenantEntity):
    """Deterministic risk scoring (Section 16).

    The model may explain risk; it never assigns it. This table records the
    factors and weights that produced the level.
    """

    __tablename__ = "risk_assessments"

    change_proposal_id: Mapped[UUID] = mapped_column(nullable=False)
    risk_level: Mapped[RiskLevel] = mapped_column(
        str_enum(RiskLevel, name="risk_level", length=16), nullable=False
    )
    numeric_score: Mapped[float] = mapped_column(Float, nullable=False)
    # The factors and their contributions. Required: risk must be explainable.
    factors: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    # Forces HIGH regardless of computed score. D-002 downstream-sync rule:
    # any change fanning out to channels after a human confirms a winner.
    escalated_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "change_proposal_id"],
            ["change_proposals.workspace_id", "change_proposals.id"],
            name="fk_risk_workspace_proposal",
            ondelete="CASCADE",
        ),
        Index("ix_risk_workspace_proposal", "workspace_id", "change_proposal_id"),
        CheckConstraint(
            "numeric_score >= 0.0 AND numeric_score <= 100.0", name="score_range"
        ),
        {"comment": "Deterministic risk scoring with factor breakdown"},
    )


class SimulationRun(SoftDeletableTenantEntity):
    """Sandbox simulation (Section 18). Never touches production."""

    __tablename__ = "simulation_runs"

    change_proposal_id: Mapped[UUID] = mapped_column(nullable=False)
    # Snapshot the simulation ran against, so results stay reproducible.
    base_catalog_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)

    schema_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    relationship_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    compliance_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    seo_sensitive: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    overall_risk: Mapped[RiskLevel | None] = mapped_column(
        str_enum(RiskLevel, name="risk_level", length=16), nullable=True
    )
    # Before/after tree diff for the UI, and a plain-language explanation.
    diff: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    impact_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "change_proposal_id"],
            ["change_proposals.workspace_id", "change_proposals.id"],
            name="fk_simulations_workspace_proposal",
            ondelete="CASCADE",
        ),
        Index("ix_simulations_workspace_proposal", "workspace_id", "change_proposal_id"),
        {"comment": "Sandbox simulation; never modifies production"},
    )


class ApprovalRequest(SoftDeletableTenantEntity):
    """Human approval gate (Section 38)."""

    __tablename__ = "approval_requests"

    change_proposal_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    # CRITICAL changes need a second approver (D-003 open item).
    required_approvals: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    approvals_received: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "change_proposal_id"],
            ["change_proposals.workspace_id", "change_proposals.id"],
            name="fk_approvals_workspace_proposal",
            ondelete="CASCADE",
        ),
        Index("ix_approvals_workspace_status", "workspace_id", "status"),
        CheckConstraint(
            "approvals_received <= required_approvals", name="not_exceeded"
        ),
        {"comment": "Human approval gate; role-gated per D-003"},
    )


class ApprovalAction(SoftDeletableTenantEntity):
    """An individual decision within an approval request."""

    __tablename__ = "approval_actions"

    approval_request_id: Mapped[UUID] = mapped_column(nullable=False)
    decided_by: Mapped[UUID] = mapped_column(nullable=False)
    decision: Mapped[ApprovalDecision] = mapped_column(
        str_enum(ApprovalDecision, name="approval_decision", length=32), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A modified approval carries the operator's edited values forward.
    modified_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "approval_request_id"],
            ["approval_requests.workspace_id", "approval_requests.id"],
            name="fk_approval_actions_workspace_request",
            ondelete="CASCADE",
        ),
        Index("ix_approval_actions_workspace_request", "workspace_id", "approval_request_id"),
        {"comment": "Individual operator decision on a change"},
    )


class ExecutionJob(SoftDeletableTenantEntity):
    """Approved change dispatched to a marketplace (Section 21)."""

    __tablename__ = "execution_jobs"

    change_proposal_id: Mapped[UUID] = mapped_column(nullable=False)
    approval_request_id: Mapped[UUID | None] = mapped_column(nullable=True)
    connector: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[ExecutionStatus] = mapped_column(
        str_enum(ExecutionStatus, name="execution_status", length=32),
        default=ExecutionStatus.PENDING,
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Bounded retries (Section 21). Never unbounded.
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    # Pre-change state. Rollback is only offered when this exists AND the
    # connector supports it; Section 51 forbids promising universal rollback.
    previous_state: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    rollback_supported: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    rollback_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)

    marketplace_response: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    recovery_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "change_proposal_id"],
            ["change_proposals.workspace_id", "change_proposals.id"],
            name="fk_execution_workspace_proposal",
            ondelete="CASCADE",
        ),
        Index("ix_execution_workspace_status", "workspace_id", "status"),
        # Section 21 forbids unbounded retries. Enforced here so no code path
        # can construct an infinite retry loop.
        CheckConstraint(
            "attempt_count <= max_attempts", name="attempts_bounded"
        ),
        {"comment": "Execution against a marketplace, with bounded retries"},
    )


class AuditLog(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """Immutable audit trail (Section 39, D-013).

    Append-only by construction.

    The mixin list is deliberately `UUIDPrimaryKeyMixin, TenantScopedMixin,
    Base` — **not** `TenantEntity`. Inheriting `TenantEntity` would bring
    `TimestampMixin.updated_at` along, and the presence of that column is what
    makes a table look mutable to a future reader. Omitting it here means no
    ORM code path can treat this table as updatable.

    A database trigger enforces the same rule independently (see migration
    0001), so immutability does not depend on the ORM at all.
    """

    __tablename__ = "audit_logs"

    # Who / what / when / why / evidence / old / new / risk / approval / result.
    actor_id: Mapped[UUID | None] = mapped_column(nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(nullable=True)

    old_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    risk_level: Mapped[RiskLevel | None] = mapped_column(
        str_enum(RiskLevel, name="risk_level", length=16), nullable=True
    )
    approval_reference: Mapped[UUID | None] = mapped_column(nullable=True)

    # D-002 evidence ledger: resolution stamp and provenance.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    execution_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Frozen at insert. Hash chain makes silent tampering detectable.
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Named separately from the `TenantScopedMixin` default
        # `ix_audit_logs_workspace_id`: two indexes on one column with one
        # name is a duplicate, and Postgres would reject the second.
        Index("ix_audit_workspace_created", "workspace_id", "created_at"),
        Index("ix_audit_workspace_entity", "workspace_id", "entity_type", "entity_id"),
        Index("ix_audit_workspace_action", "workspace_id", "action"),
        Index("ix_audit_logs_request_id", "request_id"),
        {"comment": "Append-only audit trail; UPDATE/DELETE revoked at the database"},
    )