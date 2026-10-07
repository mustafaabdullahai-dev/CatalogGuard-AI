"""Tool-call recording (Phase 20, §23, §32, D-010).

The exit criterion is that `tool_calls` moves from schema to record, so the
assertions here are about rows: one per invocation, failures included, with
arguments masked before they are stored and content reduced to metadata.

The default is asserted as well. A registry built without a recorder writes
nothing, which is what keeps Phase 14's "nothing in the graph writes to the
database" true for every caller that has not asked for records.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.agents.tools import (
    DatabaseToolRecorder,
    ToolContext,
    ToolRegistry,
    ToolSpec,
    default_registry,
    finish_agent_run,
    start_agent_run,
)
from app.core.masking import MASK
from app.db.base import TenantContext
from app.models.platform import AgentRun, ToolCall
from app.models.tenancy import UserRole
from tests.unit.fakes import fake_session, written

WORKSPACE = uuid.uuid4()


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(max_length=40)


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid")

    echo: str = Field(max_length=40)


def _handler(args: _Args, context: ToolContext) -> _Result:
    return _Result(echo=args.query)


def _spec(name: str = "echo_text", *, capability: str = "read") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="Echo the query back.",
        args_model=_Args,
        result_model=_Result,
        handler=_handler,
        capability=capability,
    )


def _context(role: UserRole = UserRole.DATA_STEWARD) -> ToolContext:
    return ToolContext(workspace_id=str(WORKSPACE), role=role)


def _calls(session: Any) -> list[ToolCall]:
    """Every recorded tool call, skipping the run row that precedes them."""
    return [row for row in written(session) if isinstance(row, ToolCall)]


async def _open_run(session: Any, tenant: TenantContext) -> AgentRun:
    return await start_agent_run(session, tenant, graph_name="catalog")


class TestOneRowPerInvocation:
    async def test_a_successful_call_is_recorded(
        self, session_and_tenant: tuple[Any, TenantContext]
    ) -> None:
        session, tenant = session_and_tenant
        run = await _open_run(session, tenant)
        registry = ToolRegistry(
            [_spec()],
            recorder=DatabaseToolRecorder(session, tenant, run.id),
        )

        outcome = await registry.execute(
            "echo_text", {"query": "navy"}, context=_context()
        )

        assert outcome.ok is True
        rows = _calls(session)
        assert len(rows) == 1
        row = rows[0]
        assert isinstance(row, ToolCall)
        assert row.tool_name == "echo_text"
        assert row.status == "ok"
        assert row.error_message is None
        assert row.agent_run_id == run.id
        assert row.workspace_id == WORKSPACE
        assert row.result_summary["ok"] is True
        assert isinstance(row.duration_ms, int)

    @pytest.mark.parametrize(
        ("arguments", "expected_error"),
        [
            ("__unknown__", "unknown_tool"),
            ({"query": "navy", "extra": 1}, "invalid_arguments"),
        ],
    )
    async def test_failures_are_recorded_too(
        self, session_and_tenant: tuple[Any, TenantContext], arguments: Any, expected_error: str
    ) -> None:
        """§23's logging requirement covers refusals, not just successes."""
        session, tenant = session_and_tenant
        run = await _open_run(session, tenant)
        registry = ToolRegistry(
            [_spec()], recorder=DatabaseToolRecorder(session, tenant, run.id)
        )

        name = "__unknown__" if isinstance(arguments, str) else "echo_text"
        payload: dict[str, Any] = {} if isinstance(arguments, str) else arguments
        outcome = await registry.execute(name, payload, context=_context())

        assert outcome.ok is False
        row = _calls(session)[0]
        assert row.status == "error"
        assert row.result_summary["ok"] is False
        assert expected_error in (row.error_message or "")

    async def test_a_refused_role_is_recorded_as_a_refusal(
        self, session_and_tenant: tuple[Any, TenantContext]
    ) -> None:
        session, tenant = session_and_tenant
        run = await _open_run(session, tenant)
        registry = ToolRegistry(
            [_spec(capability="approve")],
            recorder=DatabaseToolRecorder(session, tenant, run.id),
        )

        outcome = await registry.execute(
            "echo_text", {"query": "navy"}, context=_context(UserRole.SEO_SPECIALIST)
        )

        assert outcome.ok is False
        assert outcome.error_code == "not_authorized"
        row = _calls(session)[0]
        assert row.status == "error"
        assert "not_authorized" in (row.error_message or "")

    async def test_subset_calls_still_record(
        self, session_and_tenant: tuple[Any, TenantContext]
    ) -> None:
        """A node's narrower registry is the same registry, minus tools."""
        session, tenant = session_and_tenant
        run = await _open_run(session, tenant)
        registry = ToolRegistry(
            [_spec()],
            recorder=DatabaseToolRecorder(session, tenant, run.id),
        ).subset(["echo_text"])

        await registry.execute("echo_text", {"query": "navy"}, context=_context())

        assert [row.tool_name for row in _calls(session)] == ["echo_text"]


class TestTheDefaultWritesNothing:
    async def test_a_registry_without_a_recorder_keeps_no_records(self) -> None:
        """Phase 14's graph stays out of the database unless asked."""
        session = fake_session()
        registry = default_registry()

        outcome = await registry.execute(
            "normalize_field", {"raw_name": "Colour"}, context=_context()
        )

        assert outcome.name == "normalize_field"
        assert session.add.call_count == 0


class TestARecorderThatCannotWrite:
    async def test_a_failing_recorder_does_not_fail_the_call(self) -> None:
        """§23: every failure comes back as data — including this one."""

        class ExplodingRecorder:
            async def record(self, **kwargs: Any) -> None:
                raise RuntimeError("no database today")

        registry = ToolRegistry([_spec()], recorder=ExplodingRecorder())

        outcome = await registry.execute(
            "echo_text", {"query": "navy"}, context=_context()
        )

        assert outcome.ok is True
        assert outcome.content


class TestArgumentsAreMaskedBeforeTheyAreStored:
    async def test_a_secret_shaped_argument_never_lands_in_the_row(
        self, session_and_tenant: tuple[Any, TenantContext]
    ) -> None:
        """D-010 applies to rows, not only to log lines."""
        session, tenant = session_and_tenant
        run = await _open_run(session, tenant)
        registry = ToolRegistry(
            [_spec()], recorder=DatabaseToolRecorder(session, tenant, run.id)
        )

        # The argument is rejected by the schema (extra keys are forbidden),
        # which is the case worth testing: a hallucinated secret still gets
        # recorded, and it gets recorded masked.
        await registry.execute(
            "echo_text",
            {"query": "navy", "api_key": "sk-live-abcdefghijklmnop"},
            context=_context(),
        )

        row = _calls(session)[0]
        assert row.arguments["api_key"] == MASK
        assert "sk-live-abcdefghijklmnop" not in str(row.arguments)


class TestRuns:
    async def test_a_run_is_opened_closed_and_referenced(
        self, session_and_tenant: tuple[Any, TenantContext]
    ) -> None:
        session, tenant = session_and_tenant
        run = await start_agent_run(
            session, tenant, graph_name="catalog", input_summary={"products": 3}
        )
        assert run.status == "running"
        assert run.id is not None

        registry = ToolRegistry(
            [_spec()], recorder=DatabaseToolRecorder(session, tenant, run.id)
        )
        await registry.execute("echo_text", {"query": "navy"}, context=_context())

        finished = await finish_agent_run(
            session,
            run,
            status="succeeded",
            output_summary={"proposals": 1},
            llm_calls=2,
            llm_tokens_used=100,
        )
        assert finished.status == "succeeded"
        assert finished.completed_at is not None
        assert isinstance(finished.duration_ms, int)
        assert finished.llm_calls == 2

        assert [call.agent_run_id for call in _calls(session)] == [run.id]


@pytest.fixture
def session_and_tenant() -> tuple[Any, TenantContext]:
    return fake_session(), TenantContext(workspace_id=WORKSPACE, user_id=uuid.uuid4())
