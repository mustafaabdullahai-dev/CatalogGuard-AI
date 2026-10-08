"""The copilot endpoint (Phase 22, §31).

What is under test is the wire contract and the gate, which is where a copilot
either helps or becomes a hole: the route exists, a role without `use_copilot` is
refused, a question that cannot be asked answers 400, an over-budget caller
answers 429, and the response repeats the disclaimers instead of trusting the UI
to render them.

No model is called. The service is what reasons, and its rules are tested in
`test_copilot_service.py`; this file substitutes it the way the other endpoint
suites substitute the database — with the real HTTP layer and a fake behind it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport

from app.api.deps import AuthContext, TenantScope, get_auth_context, get_current_tenant, get_session
from app.api.v1 import copilot as copilot_api
from app.core.config import get_settings
from app.db.base import TenantContext
from app.main import app
from app.models.tenancy import User, UserRole
from app.services.copilot.agent import (
    CopilotAnswerError,
    CopilotRefusedError,
    CopilotTurn,
)
from app.services.copilot.rate_limit import CopilotRateLimitError
from app.services.copilot.schema import CopilotAnswer

PREFIX = "/api/v1"
QUERY = f"{PREFIX}/copilot/query"
WORKSPACE = uuid4()

ANSWER = CopilotAnswer.model_validate(
    {
        "answer": "Two products disagree about colour; the PIM wins on authority (D-002).",
        "citations": [
            {"tool": "conflict_search", "reference": "conflict:colour", "detail": "rank 1 vs 3"}
        ],
        "tools_used": ["conflict_search"],
        "insufficient_data": False,
        "follow_ups": ["Which marketplace is affected?"],
    }
)


def _user() -> User:
    return User(
        id=uuid4(),
        email="ops@example.com",
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
        get_session: lambda: MagicMock(),
    }


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
        follow_redirects=False,
    )


@pytest.fixture
def csrf(client: httpx.AsyncClient):
    async def _token() -> dict[str, str]:
        await client.get(f"{PREFIX}/auth/csrf")
        token = client.cookies.get(get_settings().csrf_cookie_name)
        assert token is not None
        return {get_settings().csrf_header_name: token}

    return _token


def _turn(**overrides: Any) -> CopilotTurn:
    values: dict[str, Any] = {
        "answer": ANSWER,
        "run_id": uuid4(),
        "tools_called": ["conflict_search"],
        "usage": MagicMock(
            input_tokens=120, output_tokens=40, total_tokens=160, calls=1, repairs=0
        ),
        "repaired": False,
        "tool_calls": 1,
    }
    values.update(overrides)
    return CopilotTurn(**values)


def _stub_ask(result: Any = None, error: Exception | None = None) -> None:
    async def _ask(*args: Any, **kwargs: Any) -> Any:
        if error is not None:
            raise error
        return result if result is not None else _turn()

    copilot_api.ask = _ask  # type: ignore[assignment]


class TestTheRoute:
    async def test_it_is_in_the_spec(self) -> None:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            paths = (await http.get("/openapi.json")).json()["paths"]
        assert QUERY in paths
        assert paths[QUERY]["post"]["summary"].startswith("Ask a question")

    async def test_a_copilot_capability_is_required(self) -> None:
        _stub_ask()
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="https://test"
        ) as http:
            http.cookies.set(get_settings().csrf_cookie_name, "token")
            response = await http.post(
                QUERY,
                json={"question": "conflicting colours?"},
                headers={get_settings().csrf_header_name: "token"},
            )
        # No session and no workspace header: `get_current_tenant` refuses
        # first, before the capability is ever consulted.
        assert response.status_code == 403

    @pytest.mark.parametrize(
        "role,allowed",
        [
            (UserRole.ECOMMERCE_OPS_MANAGER.value, True),
            (UserRole.DATA_STEWARD.value, True),
            (UserRole.MERCHANDISER.value, False),
            (UserRole.SEO_SPECIALIST.value, False),
            (UserRole.DATA_INTEGRATION_ADMIN.value, False),
        ],
    )
    async def test_the_capability_matrix_decides_who_asks(
        self, client, csrf, role: str, allowed: bool
    ) -> None:
        _stub_ask()
        app.dependency_overrides.update(_override(role))
        try:
            response = await client.post(
                QUERY, json={"question": "conflicting colours?"}, headers=await csrf()
            )
        finally:
            app.dependency_overrides.clear()

        if allowed:
            assert response.status_code == 200, response.text
        else:
            assert response.status_code == 403
            # The gate does not say which capability is missing: naming it
            # would tell an unauthorized caller what to acquire.
            assert response.json()["detail"] == "Insufficient permissions"

    async def test_the_response_carries_the_citations_and_the_disclaimers(
        self, client, csrf
    ) -> None:
        _stub_ask()
        app.dependency_overrides.update(_override(UserRole.DATA_STEWARD.value))
        try:
            response = await client.post(
                QUERY, json={"question": "conflicting colours?"}, headers=await csrf()
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["tools_used"] == ["conflict_search"]
        assert body["citations"][0]["tool"] == "conflict_search"
        assert body["insufficient_data"] is False
        assert body["follow_ups"] == ["Which marketplace is affected?"]
        assert "cannot create, approve or execute" in body["note"]
        assert body["usage"]["total_tokens"] == 160

    async def test_history_is_forwarded_as_context(self, client, csrf) -> None:
        seen: dict[str, Any] = {}

        async def _ask(*args: Any, **kwargs: Any) -> Any:
            seen.update(kwargs)
            return _turn()

        copilot_api.ask = _ask  # type: ignore[assignment]
        app.dependency_overrides.update(_override(UserRole.DATA_STEWARD.value))
        try:
            await client.post(
                QUERY,
                json={
                    "question": "and the risky ones?",
                    "history": [{"question": "conflicts?", "answer": "three products"}],
                },
                headers=await csrf(),
            )
        finally:
            app.dependency_overrides.clear()

        assert seen["history"] == [{"question": "conflicts?", "answer": "three products"}]


class TestRefusals:
    async def test_a_bad_question_is_400(self, client, csrf) -> None:
        _stub_ask(error=CopilotRefusedError("question is longer than 2000 characters"))
        app.dependency_overrides.update(_override(UserRole.DATA_STEWARD.value))
        try:
            response = await client.post(QUERY, json={"question": "x"}, headers=await csrf())
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 400
        assert "2000 characters" in response.json()["detail"]

    async def test_over_budget_is_429_with_a_retry_after(self, client, csrf) -> None:
        _stub_ask(error=CopilotRateLimitError(42))
        app.dependency_overrides.update(_override(UserRole.DATA_STEWARD.value))
        try:
            response = await client.post(
                QUERY, json={"question": "conflicting colours?"}, headers=await csrf()
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 429
        assert response.headers["Retry-After"] == "42"

    async def test_an_unanswerable_question_is_502_not_an_invention(self, client, csrf) -> None:
        _stub_ask(
            error=CopilotAnswerError("1 citation(s) named a tool you never called (called: none).")
        )
        app.dependency_overrides.update(_override(UserRole.DATA_STEWARD.value))
        try:
            response = await client.post(
                QUERY, json={"question": "conflicting colours?"}, headers=await csrf()
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 502
        body = response.json()
        assert "could not answer" in body["detail"]
        assert "answer" not in body, "a refusal must not carry an invented answer"

    async def test_an_empty_question_is_rejected_by_the_schema(self, client, csrf) -> None:
        _stub_ask()
        app.dependency_overrides.update(_override(UserRole.DATA_STEWARD.value))
        try:
            response = await client.post(QUERY, json={"question": ""}, headers=await csrf())
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422


class TestNoActPath:
    async def test_the_router_exposes_exactly_one_route_and_no_writes(self, client, csrf) -> None:
        paths = app.openapi()["paths"]
        copilot_paths = [path for path in paths if "copilot" in path]
        assert copilot_paths == [QUERY]
        assert set(paths[QUERY]) == {"post"}, "a copilot route that can only answer"
