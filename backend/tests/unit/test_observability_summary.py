"""The in-app summary's arithmetic (Phase 23, §32, D-007).

The endpoint is one query; the numbers it reports are the part that can quietly
lie. A p95 computed over whatever rows happened to be in memory, a token total
that conflates "no runs" with "no tokens", a failure rate that only counts one
kind of failure — each of those is a dashboard that says something untrue, and
none of them needs a database to get wrong.

Every case here is a pure function of rows, which is the property that makes the
arithmetic testable at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.models.platform import AgentRun, ToolCall
from app.models.reconciliation import ExecutionJob, ExecutionStatus
from app.services.observability import as_payload, collect, percentile, summarize

SINCE = datetime(2026, 10, 1, tzinfo=UTC)
WINDOW = 168


def _run(**overrides: Any) -> AgentRun:
    values: dict[str, Any] = {
        "id": uuid4(),
        "workspace_id": uuid4(),
        "run_type": "catalog_analysis",
        "graph_name": "catalog_pipeline",
        "status": "completed",
        "input_summary": {},
        "output_summary": {},
        "started_at": SINCE + timedelta(hours=1),
        "completed_at": SINCE + timedelta(hours=1, seconds=2),
        "duration_ms": 2000,
        "llm_calls": 1,
        "llm_tokens_used": 1500,
    }
    values.update(overrides)
    return AgentRun(**values)


def _tool_call(ok: bool = True, tool: str = "conflict_search", duration_ms: int = 40) -> ToolCall:
    return ToolCall(
        id=uuid4(),
        agent_run_id=uuid4(),
        tool_name=tool,
        arguments={},
        result_summary={"ok": ok},
        duration_ms=duration_ms,
        created_at=SINCE,
    )


def _job(status: str, attempts: int = 1) -> ExecutionJob:
    return ExecutionJob(
        id=uuid4(),
        change_proposal_id=uuid4(),
        connector="mock",
        operation="update_product",
        status=ExecutionStatus[status],
        attempt_count=attempts,
    )


def _summary(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "window_hours": WINDOW,
        "since": SINCE,
        "runs": [],
        "tool_calls": [],
        "retrieval_events": [],
        "executions": [],
    }
    values.update(overrides)
    return summarize(**values)


class TestPercentile:
    def test_nearest_rank_on_a_known_sample(self) -> None:
        sample = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]

        assert percentile(sample, 0.5) == 50
        assert percentile(sample, 0.95) == 100
        assert percentile(sample, 0.0) == 10

    def test_an_empty_sample_is_none_not_zero(self) -> None:
        assert percentile([], 0.95) is None

    def test_a_single_sample_is_both_percentiles(self) -> None:
        assert percentile([7], 0.5) == 7
        assert percentile([7], 0.95) == 7


class TestRunSummary:
    def test_counts_latency_and_tokens(self) -> None:
        summary = _summary(
            runs=[
                _run(duration_ms=100, llm_tokens_used=1000),
                _run(duration_ms=300, llm_tokens_used=2000, llm_calls=2),
                _run(duration_ms=500, llm_tokens_used=3000, status="failed"),
            ]
        )

        assert summary.agent_runs.total == 3
        assert summary.agent_runs.by_status == {"completed": 2, "failed": 1}
        assert summary.agent_runs.tokens_used == 6000
        assert summary.agent_runs.p50_duration_ms == 300
        assert summary.agent_runs.p95_duration_ms == 500

    def test_a_run_that_burned_extra_calls_repaired_something(self) -> None:
        summary = _summary(
            runs=[_run(llm_calls=1), _run(llm_calls=3), _run(llm_calls=1)]
        )

        assert summary.agent_runs.repaired == 1
        assert summary.agent_runs.llm_calls == 5

    def test_runs_with_no_duration_report_none(self) -> None:
        summary = _summary(runs=[_run(duration_ms=None), _run(duration_ms=250)])

        assert summary.agent_runs.p50_duration_ms == 250
        assert summary.agent_runs.p95_duration_ms == 250

    def test_no_runs_is_zero_and_not_an_error(self) -> None:
        summary = _summary()

        assert summary.agent_runs.total == 0
        assert summary.agent_runs.p50_duration_ms is None
        assert summary.agent_runs.tokens_used == 0


class TestToolSummary:
    def test_counts_failures_per_tool(self) -> None:
        summary = _summary(
            tool_calls=[
                _tool_call(tool="conflict_search"),
                _tool_call(tool="conflict_search", ok=False),
                _tool_call(tool="compliance_search"),
            ]
        )

        assert summary.tool_calls.total == 3
        assert summary.tool_calls.failed == 1
        assert summary.tool_calls.by_tool == {"compliance_search": 1, "conflict_search": 2}
        assert summary.tool_calls.failures_by_tool == {"conflict_search": 1}

    def test_a_missing_result_summary_is_not_counted_as_a_failure(self) -> None:
        call = ToolCall(
            id=uuid4(),
            agent_run_id=uuid4(),
            tool_name="compliance_search",
            arguments={},
            result_summary=None,
            duration_ms=10,
        )

        summary = _summary(tool_calls=[call])

        # An absent summary is an older row, not a recorded failure.
        assert summary.tool_calls.failed == 0


class TestRetrievalSummary:
    def test_untraced_retrieval_is_null_not_zero(self) -> None:
        summary = _summary()

        assert summary.retrieval.searches is None
        assert summary.retrieval.empty_results is None

    def test_traces_report_counts_empty_results_and_signals(self) -> None:
        summary = _summary(
            retrieval_events=[
                {"status": "ok", "signals": ["keyword", "metadata"], "duration_ms": 30},
                {"status": "empty", "signals": ["keyword"], "duration_ms": 90},
            ]
        )

        assert summary.retrieval.searches == 2
        assert summary.retrieval.empty_results == 1
        assert summary.retrieval.signals == {"keyword": 1, "keyword,metadata": 1}
        assert summary.retrieval.p95_duration_ms == 90


class TestExecutionSummary:
    def test_reports_outcomes_attempts_and_rollbacks(self) -> None:
        summary = _summary(
            executions=[
                _job("SUCCEEDED", attempts=1),
                _job("ROLLED_BACK", attempts=2),
                _job("PARTIALLY_FAILED", attempts=3),
                _job("FAILED", attempts=1),
            ]
        )

        assert summary.executions.total == 4
        assert summary.executions.total_attempts == 7
        assert summary.executions.rolled_back == 1
        assert summary.executions.partially_failed == 1
        assert summary.executions.by_status == {
            "failed": 1,
            "partially_failed": 1,
            "rolled_back": 1,
            "succeeded": 1,
        }


class TestHonesty:
    def test_missing_signals_are_named_rather_than_shown_as_zero(self) -> None:
        summary = _summary()

        assert summary.unavailable == [
            "no tool calls recorded in this window",
            "no executions recorded in this window",
        ]

    def test_the_payload_states_what_null_means(self) -> None:
        payload = as_payload(_summary())

        assert "not the same as zero" in payload["note"]
        assert payload["retrieval"]["searches"] is None

    def test_a_populated_workspace_needs_no_excuses(self) -> None:
        summary = _summary(runs=[_run()], tool_calls=[_tool_call()], executions=[_job("SUCCEEDED")])

        assert summary.unavailable == []


class TestCollect:
    async def test_an_empty_workspace_summarizes_to_zeros_and_nulls(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from app.db.base import TenantContext

        session = MagicMock()
        empty = MagicMock()
        empty.scalar_one.return_value = 0
        empty.scalar_one_or_none.return_value = 0
        empty.all.return_value = []
        empty.scalars.return_value = empty
        session.execute = AsyncMock(return_value=empty)

        summary = await collect(
            session, TenantContext(workspace_id=uuid4(), user_id=uuid4())
        )

        payload = as_payload(summary)
        assert payload["agent_runs"]["total"] == 0
        assert payload["retrieval"]["searches"] is None
        assert payload["window_hours"] == 168

    async def test_a_truncated_read_says_it_was_truncated(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from app.db.base import TenantContext

        session = MagicMock()
        results: list[MagicMock] = []

        def _result(rows: list[Any], scalar: int = 0) -> MagicMock:
            result = MagicMock()
            result.all.return_value = rows
            result.scalars.return_value = result
            result.scalar_one.return_value = scalar
            result.scalar_one_or_none.return_value = scalar
            return result

        # Three listing queries return rows; the count query says there are more.
        results.extend([_result([]), _result([]), _result([]), _result([], scalar=9001)])
        session.execute = AsyncMock(side_effect=results)

        summary = await collect(
            session, TenantContext(workspace_id=uuid4(), user_id=uuid4()), row_cap=10
        )

        assert any("row cap" in item for item in summary.unavailable)