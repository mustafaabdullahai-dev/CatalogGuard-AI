"""Reconciliation request and response models (Phase 12).

Built field by field rather than by dumping an ORM row. The two columns that
must never leak are `ChangeProposal.confidence` read out of context — it is
evidence, not permission (§37) — and `competing_values`, which carries supplier
catalog data. Both are still returned, because they are the content of the thing
being reviewed; what is *not* returned is any secret or raw credential.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ReconciliationResponse(BaseModel):
    run_id: UUID
    catalog_id: UUID
    catalog_version: int
    products_analyzed: int
    conflicts_found: int
    proposals_created: int
    proposals_superseded: int
    status: str
    note: str

    @classmethod
    def from_summary(cls, summary: Any, *, note: str) -> ReconciliationResponse:
        return cls(
            run_id=UUID(summary.run_id),
            catalog_id=UUID(summary.catalog_id),
            catalog_version=summary.catalog_version,
            products_analyzed=summary.products_analyzed,
            conflicts_found=summary.conflicts_found,
            proposals_created=summary.proposals_created,
            proposals_superseded=summary.proposals_superseded,
            status=summary.status,
            note=note,
        )


class ConflictRef(BaseModel):
    id: UUID
    product_id: UUID | None
    conflict_type: str
    field_name: str
    competing_values: dict[str, Any]
    confidence: float | None
    reconciliation_run_id: UUID | None
    resolved_at: datetime | None
    created_at: datetime

    @classmethod
    def from_record(cls, row: Any) -> ConflictRef:
        return cls(
            id=row.id,
            product_id=row.product_id,
            conflict_type=str(row.conflict_type),
            field_name=row.field_name,
            competing_values=dict(row.competing_values or {}),
            confidence=row.confidence,
            reconciliation_run_id=row.reconciliation_run_id,
            resolved_at=row.resolved_at,
            created_at=row.created_at,
        )


class ConflictListResponse(BaseModel):
    conflicts: list[ConflictRef]
    total: int
    limit: int
    offset: int


class ChangeProposalRef(BaseModel):
    """A §15 proposal. `confidence` is evidence, never permission."""

    id: UUID
    change_ref: str
    product_id: UUID | None
    field_name: str
    current_value: Any | None
    proposed_value: Any | None
    reason: str
    confidence: float
    risk_level: str
    requires_approval: bool
    status: str
    validation_status: str
    seo_impact: str
    compliance_impact: str
    affected_channels: list[Any]
    created_at: datetime

    @classmethod
    def from_record(cls, row: Any) -> ChangeProposalRef:
        return cls(
            id=row.id,
            change_ref=row.change_ref,
            product_id=row.product_id,
            field_name=row.field_name,
            current_value=row.current_value,
            proposed_value=row.proposed_value,
            reason=row.reason,
            confidence=row.confidence,
            risk_level=str(row.risk_level),
            requires_approval=row.requires_approval,
            status=str(row.status),
            validation_status=row.validation_status,
            seo_impact=row.seo_impact,
            compliance_impact=row.compliance_impact,
            affected_channels=list(row.affected_channels or []),
            created_at=row.created_at,
        )


class ChangeProposalListResponse(BaseModel):
    proposals: list[ChangeProposalRef]
    total: int
    limit: int
    offset: int


class ReconciliationRunRef(BaseModel):
    id: UUID
    catalog_id: UUID
    catalog_version: int
    status: str
    products_analyzed: int
    conflicts_found: int
    started_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_record(cls, row: Any) -> ReconciliationRunRef:
        return cls(
            id=row.id,
            catalog_id=row.catalog_id,
            catalog_version=row.catalog_version,
            status=row.status,
            products_analyzed=row.products_analyzed,
            conflicts_found=row.conflicts_found,
            started_at=row.started_at,
            completed_at=row.completed_at,
        )


class ReconciliationRunListResponse(BaseModel):
    runs: list[ReconciliationRunRef]
    total: int
    limit: int
    offset: int