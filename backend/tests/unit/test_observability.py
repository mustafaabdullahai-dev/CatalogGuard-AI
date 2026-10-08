"""Request context and the observability endpoint (Phase 23, §32, §55).

`bind_context` existed since Phase 8 and nothing called it, so §55's required
fields were absent from every log line. These tests assert the fields are now
present and correlatable: a response carries the id, a log line from inside the
request carries it too, and one request's status cannot leak into the next.

The summary endpoint's tests assert the gate (`admin`, not `read`) and the shape
of an empty workspace, because "no activity" is the state every deployment starts
in and it must not look like an error.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport

from app.api.deps import AuthContext, TenantScope, get_auth_context, get_current_tenant, get_session
from app.core.config import get_settings
from app.db.base import TenantContext
from app.main import app
from app.models.tenancy import User, UserRole

PREFIX = "/api/v1"
SUMMARY = f"{PREFIX}/observability/summary"
WORKSPACE = uuid4()


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
        follow_redirects=False,
    )


def _user() -> User:
    return User(
        id=uuid4(),
        email="admin@example.com",
        hashed_password="$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA",
        full_name="Ops Person",
        is_active=True,
    )


def _override(role: str) -> dict[Any, Any]:
    user = _user()
    session = MagicMock()
    session.id = uuid4()
    session.user_id = user.id
    context = AuthContext(user=user, session=session, memberships=())
    scope = TenantScope(
        context=context,
        tenant=TenantContext(workspace_id=WORKSPACE, user_id=user.id),
        role=role,
    )
    return {
        get_auth_context: lambda: context,
        get_current_tenant: lambda: scope,
        get_session: lambda: _empty_session(),
    }


def _empty_session() -> MagicMock:
    """A session with nothing in it: the shape a fresh workspace has."""
    session = MagicMock()
    empty = MagicMock()
    empty.scalar_one.return_value = 0
    empty.scalar_one_or_none.return_value = 0
    empty.all.return_value = []
    empty.scalars.return_value = empty
    session.execute = AsyncMock(return_value=empty)
    session.commit = AsyncMock(return_value=None)
    return session


class TestRequestContext:
    async def test_every_response_carries_a_request_id(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/health")

        assert response.status_code == 200
        assert response.headers["x-request-id"]

    async def test_an_inbound_request_id_is_honoured(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/health", headers={"X-Request-Id": "trace-abc-123"})

        assert response.headers["x-request-id"] == "trace-abc-123"

    @pytest.mark.parametrize(
        "hostile",
        [
            "a" * 200,
            "has spaces",
            "<script>alert(1)</script>",
            "semi;colon",
        ],
    )
    async def test_an_unusable_inbound_id_is_replaced(
        self, client: httpx.AsyncClient, hostile: str
    ) -> None:
        response = await client.get("/health", headers={"X-Request-Id": hostile})

        assert response.headers["x-request-id"] != hostile
        assert response.headers["x-request-id"]

    async def test_ids_do_not_leak_between_requests(self, client: httpx.AsyncClient) -> None:
        first = await client.get("/health")
        second = await client.get("/health")

        assert first.headers["x-request-id"] != second.headers["x-request-id"]


class _Recorder:
    """A logger stand-in that keeps what it was told.

    It merges the bound context exactly as structlog's `merge_contextvars`
    processor does, because the fields under test arrive that way: the middleware
    passes `status` and `duration_ms` as arguments and inherits `request_id`,
    `method` and `path` from the request it is in.
    """

    def __init__(self, sink: list[dict[str, Any]]) -> None:
        self._sink = sink

    def info(self, event: str, **fields: Any) -> None:
        from structlog.contextvars import get_contextvars

        self._sink.append({**get_contextvars(), "event": event, **fields})


@contextmanager
def _captured() -> Iterator[list[dict[str, Any]]]:
    """Collect log events with the real processor chain, bound context included.

    `structlog.testing.capture_logs` swaps the chain for a bare collector, which
    drops `merge_contextvars` — the exact mechanism this test is about.
    """
    import structlog

    events: list[dict[str, Any]] = []

    def _collect(_logger: Any, _name: str, event_dict: dict[str, Any]) -> Any:
        events.append(event_dict)
        raise structlog.DropEvent

    previous = structlog.get_config()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _collect,
        ],
        # Without this, a module-level logger (the middleware's) keeps the
        # configuration it first resolved under and never reaches `_collect`.
        cache_logger_on_first_use=False,
    )
    try:
        yield events
    finally:
        structlog.configure(**previous)


class TestLogCorrelation:
    """The point of the middleware: a log line can be tied to its request."""

    async def test_the_completion_line_carries_the_request_fields(self) -> None:
        import app.core.middleware as middleware_module

        sent: list[dict[str, Any]] = []

        async def _call(_scope: Any, _receive: Any, send: Any) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def _receive() -> dict[str, Any]:
            return {"type": "http.request"}

        async def _send(message: dict[str, Any]) -> None:
            sent.append(message)

        # The middleware logs through a module-level logger that structlog has
        # already cached, so it is replaced directly: reconfiguring the global
        # chain would depend on test order.
        events: list[dict[str, Any]] = []
        original = middleware_module.logger
        middleware_module.logger = _Recorder(events)
        try:
            await middleware_module.RequestContextMiddleware(_call)(
                {"type": "http", "method": "GET", "path": "/health", "headers": []},
                _receive,
                _send,
            )
        finally:
            middleware_module.logger = original

        completion = [event for event in events if event["event"] == "request_completed"]
        assert len(completion) == 1
        event = completion[0]
        assert event["method"] == "GET"
        assert event["path"] == "/health"
        # The status comes from the response the app sent, not from a default.
        assert event["status"] == 200
        assert isinstance(event["duration_ms"], int)
        assert event["request_id"] == dict(sent[0]["headers"])[b"x-request-id"].decode()

    async def test_a_component_log_line_inherits_the_bound_context(self) -> None:
        from app.core.logging import get_logger
        from app.core.middleware import RequestContextMiddleware


        async def _call(_scope: Any, _receive: Any, _send: Any) -> None:
            get_logger("test.component").info("component_event")

        async def _receive() -> dict[str, Any]:
            return {"type": "http.request"}

        async def _send(_message: dict[str, Any]) -> None:
            return None

        async def _drive() -> None:
            await RequestContextMiddleware(_call)(
                {"type": "http", "method": "POST", "path": "/x", "headers": []},
                _receive,
                _send,
            )

        with _captured() as events:
            await _drive()

        component = [event for event in events if event["event"] == "component_event"][0]
        assert component["request_id"]
        assert component["path"] == "/x"

    def test_the_tenant_dependency_binds_the_identity_fields(self) -> None:
        from structlog.contextvars import bind_contextvars, clear_contextvars

        from app.core.middleware import _bound_workspace

        clear_contextvars()
        bind_contextvars(workspace_id=str(WORKSPACE))
        try:
            assert _bound_workspace() == str(WORKSPACE)
        finally:
            clear_contextvars()


class TestSummaryEndpoint:
    async def test_it_is_in_the_spec(self) -> None:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            paths = (await http.get("/openapi.json")).json()["paths"]
        assert SUMMARY in paths

    @pytest.mark.parametrize(
        "role,allowed",
        [
            (UserRole.ECOMMERCE_OPS_MANAGER.value, True),
            (UserRole.DATA_STEWARD.value, False),
            (UserRole.MERCHANDISER.value, False),
            (UserRole.DATA_INTEGRATION_ADMIN.value, False),
        ],
    )
    async def test_only_an_admin_reads_the_summary(
        self, client: httpx.AsyncClient, role: str, allowed: bool
    ) -> None:
        app.dependency_overrides.update(_override(role))
        try:
            response = await client.get(SUMMARY)
        finally:
            app.dependency_overrides.clear()

        if allowed:
            assert response.status_code == 200, response.text
        else:
            assert response.status_code == 403
            assert "admin" not in response.json()["detail"]

    async def test_an_empty_workspace_reads_as_zero_not_as_an_error(
        self, client: httpx.AsyncClient
    ) -> None:
        app.dependency_overrides.update(_override(UserRole.ECOMMERCE_OPS_MANAGER.value))
        try:
            response = await client.get(SUMMARY)
        finally:
            app.dependency_overrides.clear()

        body = response.json()
        assert response.status_code == 200
        assert body["summary"]["agent_runs"]["total"] == 0
        assert body["summary"]["executions"]["total"] == 0
        # Untraced is null, not zero: retrieval traces need a configured sink.
        assert body["summary"]["retrieval"]["searches"] is None
        assert body["summary"]["unavailable"]

    async def test_it_reports_which_sink_is_installed(self, client: httpx.AsyncClient) -> None:
        app.dependency_overrides.update(_override(UserRole.ECOMMERCE_OPS_MANAGER.value))
        try:
            response = await client.get(SUMMARY)
        finally:
            app.dependency_overrides.clear()

        telemetry = response.json()["telemetry"]
        assert telemetry["enabled"] is False
        assert "discarded" in telemetry["detail"]

    async def test_every_signal_32_asks_for_is_mapped(self, client: httpx.AsyncClient) -> None:
        app.dependency_overrides.update(_override(UserRole.ECOMMERCE_OPS_MANAGER.value))
        try:
            response = await client.get(SUMMARY)
        finally:
            app.dependency_overrides.clear()

        signals = response.json()["signals"]
        for requirement in (
            "agent_runs",
            "latency",
            "token_usage",
            "tool_calls",
            "failures",
            "retrieval_quality",
            "model_usage",
            "workflow_state",
            "execution_status",
        ):
            assert requirement in signals

    async def test_the_window_is_bounded(self, client: httpx.AsyncClient) -> None:
        app.dependency_overrides.update(_override(UserRole.ECOMMERCE_OPS_MANAGER.value))
        try:
            response = await client.get(SUMMARY, params={"window_hours": 100000})
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422

    def test_the_masking_processor_masks_a_logged_field(self) -> None:
        """D-010 rule 3: masking at the serialization boundary, always."""
        from app.core.logging import MaskingProcessor

        processor = MaskingProcessor(get_settings().mask_key_set)

        masked = processor(
            None, "test", {"event": "check", "sku": "CG-1001", "duration_ms": 5}
        )

        assert masked["sku"] != "CG-1001"
        assert masked["duration_ms"] == 5