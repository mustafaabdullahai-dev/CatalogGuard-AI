"""Sandbox simulation (Phase 17, §18, §19, §17).

The engine that predicts a change's impact without making it, and the seam
that writes what it found. Never touches production — §18's first line.
"""

from __future__ import annotations

from app.services.simulation.persistence import (
    persist_simulation,
    simulation_run_for,
)
from app.services.simulation.sandbox import (
    NOT_CONFIGURED,
    CatalogSnapshot,
    ComplianceCheck,
    ComplianceOutcome,
    ProductState,
    ProposedChange,
    SimulationResult,
    StageOutcome,
    simulate,
    validation_for,
)

__all__ = [
    "NOT_CONFIGURED",
    "CatalogSnapshot",
    "ComplianceCheck",
    "ComplianceOutcome",
    "ProductState",
    "ProposedChange",
    "SimulationResult",
    "StageOutcome",
    "persist_simulation",
    "simulation_run_for",
    "simulate",
    "validation_for",
]
