"""In-app observability summary (Phase 23, §32, D-007).

D-010 rules out hosted observability products, and D-007 mandates token and cost
accounting *in the app* because there is no vendor dashboard to read it from.
This is that accounting: an aggregation over the rows the system already writes —
`agent_runs`, `tool_calls`, `execution_jobs` — so "what did this workspace ask
the model last week, what failed, and which tool was slow" is a query rather than
an archaeology project.

Two deliberate constraints:

- **The arithmetic is pure.** `summarize(...)` takes rows and returns the summary,
  so every number below is unit tested without a database. `collect(...)` is the
  thin part that reads.
- **A number with no rows is `0`, and a signal nobody emits is `null`.** The
  difference matters: `tool_calls=0` means no tools ran, while
  `llm_calls=None` means this deployment has no LLM configured at all. Collapsing
  them would make "we spent nothing" and "there was nothing to spend on" the
  same sentence.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import TenantContext
from app.models.platform import AgentRun, ToolCall
from app.models.reconciliation import ExecutionJob, ExecutionStatus


def percentile(values: Sequence[float], fraction: float) -> int | None:
    """The nearest-rank percentile, or `None` for an empty sample.

    Nearest-rank rather than interpolated: an operator reading "p95 latency 812ms"
    should be able to point at the request that took 812ms, and interpolation
    cannot.
    """
    if not values:
        return None
    ordered = sorted(values)
    if fraction <= 0:
        return int(ordered[0])
    if fraction >= 1:
        return int(ordered[-1])
    rank = max(1, min(len(ordered), int(-(-fraction * len(ordered) // 1))))
    return int(ordered[rank - 1])


@dataclass(frozen=True, slots=True)
class RunSummary:
    """Agent runs over a window: counts, latency, tokens."""

    total: int
    by_status: dict[str, int] = field(default_factory=dict)
    by_type: dict[str, int] = field(default_factory=dict)
    p50_duration_ms: int | None = None
    p95_duration_ms: int | None = None
    #: The column the schema has is a single `llm_tokens_used`; splitting it
    #: per direction would be a number nothing recorded, so it stays a total.
    tokens_used: int = 0
    llm_calls: int = 0
    #: Runs where the model's output needed a second attempt (D-007 #3).
    repaired: int = 0


@dataclass(frozen=True, slots=True)
class ToolSummary:
    """Tool calls: per-tool volume and failure counts."""

    total: int
    failed: int
    by_tool: dict[str, int] = field(default_factory=dict)
    failures_by_tool: dict[str, int] = field(default_factory=dict)
    p95_duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class RetrievalSummary:
    """Compliance retrieval: volume, empty results, and which signals fired."""

    searches: int | None
    empty_results: int | None
    signals: dict[str, int] = field(default_factory=dict)
    #: `None` when no search has been traced, which is not the same as zero.
    p95_duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ExecutionSummary:
    """Executions: outcomes, attempts, rollbacks (§21)."""

    total: int
    by_status: dict[str, int] = field(default_factory=dict)
    total_attempts: int = 0
    rolled_back: int = 0
    partially_failed: int = 0


@dataclass(frozen=True, slots=True)
class ObservabilitySummary:
    window_hours: int
    since: datetime
    agent_runs: RunSummary
    tool_calls: ToolSummary
    retrieval: RetrievalSummary
    executions: ExecutionSummary
    #: What this deployment cannot report, stated rather than shown as zero.
    unavailable: list[str] = field(default_factory=list)
    note: str = (
        "Counts over rows this deployment wrote. Signals marked null have not been "
        "observed yet, which is not the same as zero."
    )


def summarize(
    *,
    window_hours: int,
    since: datetime,
    runs: Sequence[AgentRun],
    tool_calls: Sequence[ToolCall],
    retrieval_events: Sequence[dict[str, Any]],
    executions: Sequence[ExecutionJob],
) -> ObservabilitySummary:
    """The whole summary as a pure function of rows. No clock, no database."""
    run_durations = [run.duration_ms for run in runs if run.duration_ms is not None]
    run_summary = RunSummary(
        total=len(runs),
        by_status=_count_by(runs, lambda run: str(run.status)),
        by_type=_count_by(runs, lambda run: str(run.run_type)),
        p50_duration_ms=percentile(run_durations, 0.5),
        p95_duration_ms=percentile(run_durations, 0.95),
        tokens_used=sum(run.llm_tokens_used or 0 for run in runs),
        llm_calls=sum(run.llm_calls or 0 for run in runs),
        # A run that burned more calls than it made attempts repaired something.
        repaired=sum(1 for run in runs if (run.llm_calls or 0) > 1),
    )

    durations = [call.duration_ms for call in tool_calls if call.duration_ms is not None]
    tool_summary = ToolSummary(
        total=len(tool_calls),
        failed=sum(1 for call in tool_calls if not _tool_ok(call)),
        by_tool=_count_by(tool_calls, lambda call: str(call.tool_name)),
        failures_by_tool=_count_by(
            (call for call in tool_calls if not _tool_ok(call)),
            lambda call: str(call.tool_name),
        ),
        p95_duration_ms=percentile(durations, 0.95),
    )

    retrieval_summary = (
        RetrievalSummary(
            searches=len(retrieval_events),
            empty_results=sum(1 for event in retrieval_events if event.get("status") == "empty"),
            signals=_count_by(
                retrieval_events,
                lambda event: ",".join(event.get("signals") or ["none"]),
            ),
            p95_duration_ms=percentile(
                [
                    event["duration_ms"]
                    for event in retrieval_events
                    if event.get("duration_ms") is not None
                ],
                0.95,
            ),
        )
        if retrieval_events
        else RetrievalSummary(
            # Never traced yet: null, because "no searches" and "searches we
            # cannot see" are different statements.
            searches=None,
            empty_results=None,
            signals={},
            p95_duration_ms=None,
        )
    )

    # One source for the statuses and the counters: comparing `str(status)` to a
    # member name while `by_status` counted values made every rollback read as
    # zero, which is the sort of quiet lie a dashboard exists to prevent.
    statuses = _count_by(executions, lambda job: str(ExecutionStatus(job.status)))
    execution_summary = ExecutionSummary(
        total=len(executions),
        by_status=statuses,
        total_attempts=sum(job.attempt_count or 0 for job in executions),
        rolled_back=statuses.get(ExecutionStatus.ROLLED_BACK.value, 0),
        partially_failed=statuses.get(ExecutionStatus.PARTIALLY_FAILED.value, 0),
    )

    unavailable: list[str] = []
    if not tool_calls:
        unavailable.append("no tool calls recorded in this window")
    if not executions:
        unavailable.append("no executions recorded in this window")

    return ObservabilitySummary(
        window_hours=window_hours,
        since=since,
        agent_runs=run_summary,
        tool_calls=tool_summary,
        retrieval=retrieval_summary,
        executions=execution_summary,
        unavailable=unavailable,
    )


def _tool_ok(call: ToolCall) -> bool:
    summary = call.result_summary or {}
    return summary.get("ok") is not False


def _count_by(rows: Iterable[Any], key: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        name = key(row)
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# reading


async def collect(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    window_hours: int = 168,
    now: datetime | None = None,
    row_cap: int = 2000,
) -> ObservabilitySummary:
    """Read the window's rows and summarize them.

    `row_cap` bounds what is read into memory and what the percentiles see. It is
    reported in `unavailable` when it bites, because a p95 computed over the
    first 2000 rows is a different number from one over the window.
    """
    from app.db.base import utcnow

    moment = now or utcnow()
    since = moment - timedelta(hours=window_hours)
    workspace = tenant.workspace_id

    runs = list(
        (
            await session.execute(
                select(AgentRun)
                .where(AgentRun.workspace_id == workspace, AgentRun.started_at >= since)
                .order_by(AgentRun.started_at.desc())
                .limit(row_cap)
            )
        )
        .scalars()
        .all()
    )
    # `IN ()` is invalid SQL and an empty window is normal, so a value no run
    # can hold stands in for "no runs" and matches nothing.
    never = uuid4()
    run_ids = [run.id for run in runs] or [never]
    tool_calls = list(
        (
            await session.execute(
                select(ToolCall)
                .where(ToolCall.agent_run_id.in_(run_ids))
                .limit(row_cap)
            )
        )
        .scalars()
        .all()
    )
    executions = list(
        (
            await session.execute(
                select(ExecutionJob)
                .where(ExecutionJob.workspace_id == workspace, ExecutionJob.created_at >= since)
                .limit(row_cap)
            )
        )
        .scalars()
        .all()
    )
    total_tools = int(
        (
            await session.execute(
                select(func.count())
                .select_from(ToolCall)
                .where(ToolCall.agent_run_id.in_(run_ids))
            )
        ).scalar_one()
    )

    summary = summarize(
        window_hours=window_hours,
        since=since,
        runs=runs,
        tool_calls=tool_calls,
        # Retrieval traces live in the telemetry sink, not in a table; until a
        # sink is configured this section reports null and says so.
        retrieval_events=[],
        executions=executions,
    )
    if total_tools > len(tool_calls):
        return replace(
            summary,
            unavailable=[
                *summary.unavailable,
                f"tool statistics cover {len(tool_calls)} of {total_tools} calls (row cap)",
            ],
        )
    return summary


def as_payload(summary: ObservabilitySummary) -> dict[str, Any]:
    """The wire shape, so the schema and the tests share one definition."""
    return {
        "window_hours": summary.window_hours,
        "since": summary.since.isoformat(),
        "agent_runs": {
            "total": summary.agent_runs.total,
            "by_status": summary.agent_runs.by_status,
            "by_type": summary.agent_runs.by_type,
            "p50_duration_ms": summary.agent_runs.p50_duration_ms,
            "p95_duration_ms": summary.agent_runs.p95_duration_ms,
            "tokens_used": summary.agent_runs.tokens_used,
            "llm_calls": summary.agent_runs.llm_calls,
            "repaired": summary.agent_runs.repaired,
        },
        "tool_calls": {
            "total": summary.tool_calls.total,
            "failed": summary.tool_calls.failed,
            "by_tool": summary.tool_calls.by_tool,
            "failures_by_tool": summary.tool_calls.failures_by_tool,
            "p95_duration_ms": summary.tool_calls.p95_duration_ms,
        },
        "retrieval": {
            "searches": summary.retrieval.searches,
            "empty_results": summary.retrieval.empty_results,
            "signals": summary.retrieval.signals,
            "p95_duration_ms": summary.retrieval.p95_duration_ms,
        },
        "executions": {
            "total": summary.executions.total,
            "by_status": summary.executions.by_status,
            "total_attempts": summary.executions.total_attempts,
            "rolled_back": summary.executions.rolled_back,
            "partially_failed": summary.executions.partially_failed,
        },
        "unavailable": summary.unavailable,
        "note": summary.note,
    }


__all__ = [
    "ObservabilitySummary",
    "as_payload",
    "collect",
    "percentile",
    "summarize",
]
