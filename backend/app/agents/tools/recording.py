"""Recording tool invocations (Phase 20, §23, §32, D-010).

`tool_calls` existed in the schema with no writer. §23 asks every tool to log,
and Phase 15 answered that with a structured line per execution — useful while
watching a run, useless afterwards: a line in a log stream cannot be joined to
a run, counted per tool, or shown next to the proposals that run produced.

So the record moves to a table, and the port sits at the one place every tool
call already goes through: `ToolRegistry.execute`. A registry built without a
recorder behaves exactly as it did before, which keeps Phase 14's "nothing in
the graph writes to the database" true by default — the graph stays DB-free
until someone hands it a recording registry.

What is stored, and what is not
    Arguments are masked (D-010) before they are stored, and the result is
    stored as metadata — ok flag, error code, character count — never as the
    tool's content. Tool content is catalog data: §55 keeps it out of logs, and
    a record of *that a call happened* does not need the payload to prove it.
    This is the same reasoning `tool_calls`' own comment states.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools.spec import ToolCallOutcome
from app.core.logging import get_logger
from app.core.masking import mask_text, mask_value
from app.core.telemetry import emit
from app.db.base import TenantContext, utcnow
from app.db.repository import TenantRepository
from app.models.platform import AgentRun, ToolCall

logger = get_logger(__name__)

RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
ABORTED = "aborted"


class AgentRunRepository(TenantRepository[AgentRun]):
    model = AgentRun


class ToolCallRepository(TenantRepository[ToolCall]):
    model = ToolCall


class DatabaseToolRecorder:
    """Writes one `tool_calls` row per invocation.

    Never raises: a row that cannot be written must not turn a tool call the
    model is waiting for into a failed run. The failure is logged and the
    outcome returned, exactly as `OutboxDelivery` treats a send it could not
    make — the record is a record, not a precondition for the answer.
    """

    def __init__(
        self, db: AsyncSession, tenant: TenantContext, agent_run_id: Any
    ) -> None:
        self._db = db
        self._tenant = tenant
        self._agent_run_id = agent_run_id

    async def record(
        self, *, name: str, arguments: Mapping[str, Any], outcome: ToolCallOutcome
    ) -> None:
        try:
            emit(
                "tool_call",
                name,
                status="ok" if outcome.ok else f"failed:{outcome.error_code}",
                workspace_id=str(self._tenant.workspace_id),
                run_id=str(self._agent_run_id),
                attributes={
                    "tool": name,
                    "ok": outcome.ok,
                    "duration_ms": outcome.duration_ms,
                    "error_code": outcome.error_code,
                },
            )
            row = ToolCall(
                agent_run_id=self._agent_run_id,
                tool_name=name,
                arguments=_jsonable(mask_value(dict(arguments))),
                result_summary={
                    "ok": outcome.ok,
                    "error_code": outcome.error_code,
                    "content_chars": len(outcome.content),
                },
                status="ok" if outcome.ok else "error",
                error_message=(
                    None
                    if outcome.ok
                    else f"{outcome.error_code}: {mask_text(outcome.content)[:200]}"
                ),
                duration_ms=outcome.duration_ms,
            )
            await ToolCallRepository(self._db, self._tenant).add(row)
        except Exception as exc:  # noqa: BLE001 - a log row never fails a tool
            logger.warning(
                "tool_call_record_failed",
                tool=name,
                error_type=type(exc).__name__,
                workspace_id=str(self._tenant.workspace_id),
            )


async def start_agent_run(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    graph_name: str,
    run_type: str = "catalog_analysis",
    input_summary: dict[str, Any] | None = None,
) -> AgentRun:
    """Open a run so tool calls have something to point at.

    `tool_calls.agent_run_id` is not nullable: a tool call with no run is a row
    nobody can place. This is why recording starts here rather than wherever a
    tool happens to be called.
    """
    emit(
        "agent_run",
        f"started:{run_type}",
        status="running",
        workspace_id=str(tenant.workspace_id),
        run_id=None,
        attributes={"run_type": run_type, "graph": graph_name},
    )
    run = AgentRun(
        run_type=run_type,
        graph_name=graph_name,
        status=RUNNING,
        input_summary=input_summary,
        # Set in Python rather than left to the column default: a run's start
        # time is needed to compute its duration on close, and a value only the
        # database knows about is a value no caller can use.
        started_at=utcnow(),
    )
    return await AgentRunRepository(db, tenant).add(run)


async def finish_agent_run(
    db: AsyncSession,
    run: AgentRun,
    *,
    status: str,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    error_message: str | None = None,
    output_summary: dict[str, Any] | None = None,
    node_trace: list[Any] | None = None,
    llm_calls: int = 0,
    llm_tokens_used: int = 0,
    duration_ms: int | None = None,
) -> AgentRun:
    """Close a run with the summary the UI shows (§32: concise, never thought)."""
    run.status = status
    run.error_message = error_message
    run.output_summary = output_summary
    if node_trace is not None:
        run.node_trace = node_trace
    run.llm_calls = llm_calls
    run.llm_tokens_used = llm_tokens_used
    if completed_at is not None:
        run.completed_at = completed_at
    elif run.completed_at is None:
        run.completed_at = utcnow()
    reference = started_at or run.started_at
    if duration_ms is None and reference is not None and run.completed_at is not None:
        run.duration_ms = int(
            (run.completed_at - reference).total_seconds() * 1000
        )
    else:
        run.duration_ms = duration_ms
    emit(
        "agent_run",
        f"finished:{status}",
        duration_ms=run.duration_ms,
        status=status,
        workspace_id=str(run.workspace_id),
        run_id=str(run.id),
        attributes={
            "run_type": run.run_type,
            "graph": run.graph_name,
            "llm_calls": run.llm_calls,
            "llm_tokens_used": run.llm_tokens_used,
            "error": run.error_message is not None,
        },
    )
    await db.flush()
    return run


def _jsonable(value: Any) -> Any:
    """Reduce masked arguments to what a JSON column can hold."""
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
