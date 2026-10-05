"""Tenant-scoped repositories for the reconciliation phase (D-011).

One subclass per model so the binding is written once and no call site has to
remember which tables are tenant-owned.
"""

from __future__ import annotations

from app.db.repository import TenantRepository
from app.models.catalog import MarketplaceProduct
from app.models.reconciliation import (
    ChangeProposal,
    Conflict,
    ReconciliationRun,
    RiskAssessment,
)


class MarketplaceProductRepository(TenantRepository[MarketplaceProduct]):
    model = MarketplaceProduct


class ReconciliationRunRepository(TenantRepository[ReconciliationRun]):
    model = ReconciliationRun


class ConflictRepository(TenantRepository[Conflict]):
    model = Conflict


class ChangeProposalRepository(TenantRepository[ChangeProposal]):
    model = ChangeProposal


class RiskAssessmentRepository(TenantRepository[RiskAssessment]):
    model = RiskAssessment