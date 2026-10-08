"""Observability endpoints (Phase 23, §32).

One read: what this workspace's system did, and what this deployment cannot see.
Gated on `admin` rather than `read` — the summary is cross-cutting operational
data (token spend, failure rates, which tool is failing), not ordinary catalog
data, and D-003's data-integration admin holds credential custody rather than
this.

There is no write here. An endpoint that could clear telemetry, silence an alert
or change a sink would be a way to make a system look healthy.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import TenantScope, require_capability
from app.core.config import Settings, get_settings
from app.core.telemetry import NullTraceSink, trace_sink
from app.db.session import get_session
from app.schemas.observability import (
    AgentRunSummary,
    ExecutionSummaryOut,
    ObservabilityOverview,
    ObservabilitySummaryOut,
    RetrievalSummaryOut,
    TelemetryStatus,
    ToolCallSummary,
)
from app.services.observability import as_payload, collect

router = APIRouter(tags=["observability"])

# §32's list, mapped to what this deployment can actually observe. Keeping the
# mapping in the response means a reader can tell which requirement is unmet
# without reading the phase page.
SIGNALS: dict[str, str] = {
    "agent_runs": "agent_runs rows (Phase 20)",
    "latency": "duration_ms on runs and tool calls",
    "token_usage": "agent_runs.llm_tokens_used (total, not split by direction)",
    "tool_calls": "tool_calls rows (Phase 20)",
    "failures": "run statuses, tool result summaries, execution statuses",
    "retrieval_quality": "retrieval traces: hit counts and signals, query text never",
    "model_usage": "llm_call traces: model id and tier per call",
    "workflow_state": "structured logs with request/workspace/run ids (§55)",
    "execution_status": "execution_jobs rows (Phase 20)",
}


@router.get(
    "/observability/summary",
    response_model=ObservabilityOverview,
    summary="Activity, latency, token usage and failures for this workspace (§32)",
)
async def observability_summary(
    scope: Annotated[TenantScope, Depends(require_capability("admin"))],
    db: Annotated[AsyncSession, Depends(get_session)],
    window_hours: Annotated[int, Query(ge=1, le=720)] = 168,
    settings: Settings = Depends(get_settings),
) -> ObservabilityOverview:
    """One window, one payload. Seven days by default; a month is the ceiling."""
    summary = await collect(db, scope.tenant, window_hours=window_hours)
    payload = as_payload(summary)
    sink = trace_sink(settings)
    return ObservabilityOverview(
        summary=ObservabilitySummaryOut(
            window_hours=payload["window_hours"],
            since=payload["since"],
            agent_runs=AgentRunSummary(**payload["agent_runs"]),
            tool_calls=ToolCallSummary(**payload["tool_calls"]),
            retrieval=RetrievalSummaryOut(**payload["retrieval"]),
            executions=ExecutionSummaryOut(**payload["executions"]),
            unavailable=payload["unavailable"],
            note=payload["note"],
        ),
        telemetry=_telemetry_status(sink, settings),
        signals=dict(SIGNALS),
    )


def _telemetry_status(sink: object, settings: Settings) -> TelemetryStatus:
    sending = not isinstance(sink, NullTraceSink)
    destination = settings.langfuse_host if sending and settings.langfuse_host else None
    if not settings.langfuse_enabled:
        detail = (
            "Tracing is off. Events are counted and discarded; structured logs still "
            "carry request_id, workspace_id and duration (§55)."
        )
    elif not sending:
        detail = (
            "Langfuse is enabled but not configured with a host and both keys, so "
            "events are still being discarded."
        )
    else:
        detail = (
            "Sending to the configured self-hosted Langfuse (D-010). Never verified "
            "in this repository: no Langfuse instance has been reachable."
        )
    return TelemetryStatus(
        sink=type(sink).__name__,
        enabled=sending,
        destination=destination,
        detail=detail,
    )
