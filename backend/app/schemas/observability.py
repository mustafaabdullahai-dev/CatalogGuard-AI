"""Observability read models (Phase 23, §32).

Flat dictionaries on purpose: this response is read by an operator and by a
script, and a nested schema whose fields move between releases is worse than a
stable flat one. `unavailable` and `note` are part of the contract, not
decoration — a summary that cannot see something says which thing, so a reader
never has to guess whether a zero means "none" or "not observed".
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    by_type: dict[str, int] = Field(default_factory=dict)
    p50_duration_ms: int | None = None
    p95_duration_ms: int | None = None
    tokens_used: int = 0
    llm_calls: int = 0
    repaired: int = 0


class ToolCallSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = 0
    failed: int = 0
    by_tool: dict[str, int] = Field(default_factory=dict)
    failures_by_tool: dict[str, int] = Field(default_factory=dict)
    p95_duration_ms: int | None = None


class RetrievalSummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: `None` when this deployment has traced no search, which is not zero.
    searches: int | None = None
    empty_results: int | None = None
    signals: dict[str, int] = Field(default_factory=dict)
    p95_duration_ms: int | None = None


class ExecutionSummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    total_attempts: int = 0
    rolled_back: int = 0
    partially_failed: int = 0


class ObservabilitySummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_hours: int
    since: datetime
    agent_runs: AgentRunSummary
    tool_calls: ToolCallSummary
    retrieval: RetrievalSummaryOut
    executions: ExecutionSummaryOut
    unavailable: list[str] = Field(default_factory=list)
    note: str


class TelemetryStatus(BaseModel):
    """Which sink is installed, and whether it is sending anywhere."""

    model_config = ConfigDict(extra="forbid")

    sink: str
    enabled: bool
    destination: str | None = None
    detail: str


class ObservabilityOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: ObservabilitySummaryOut
    telemetry: TelemetryStatus
    signals: dict[str, Any] = Field(default_factory=dict)