"""HTTP surface tests.

The application is a FastAPI app, so the routes are the contract. These tests
run in-process against ASGI with no server and no network.

The logo matters more than a placeholder usually would: D-001 makes the mark the
product's visual identity, and a 404 on `/favicon.ico` is the most visible
possible regression. The byte-identity check also catches the copy drifting out
of sync with `frontend/public/logo.png`, which nothing else notices.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app

FRONTEND_LOGO = (
    Path(__file__).resolve().parents[3] / "frontend" / "public" / "logo.png"
)
BACKEND_LOGO = (
    Path(__file__).resolve().parents[2] / "app" / "static" / "logo.png"
)


@pytest.fixture
def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    )


class TestLogoRoutes:
    async def test_api_logo_serves_a_png(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/brand/logo.png")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_favicon_serves_a_png(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/favicon.ico")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    async def test_static_mount_serves_a_png(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/static/logo.png")
        assert response.status_code == 200
        assert response.content[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_backend_copy_matches_the_frontend_original(
        self, client: httpx.AsyncClient
    ) -> None:
        """One canonical file, three routes.

        The backend copy exists so the API can serve the mark. If it drifts, the
        frontend and the API show different logos and nothing notices.
        """
        response = await client.get("/api/v1/brand/logo.png")
        assert sha256(response.content).digest() == sha256(
            FRONTEND_LOGO.read_bytes()
        ).digest()

    def test_static_copy_is_not_missing(self) -> None:
        assert BACKEND_LOGO.exists()
        assert BACKEND_LOGO.read_bytes() == FRONTEND_LOGO.read_bytes()


class TestHealth:
    async def test_liveness_answers_from_the_process_alone(
        self, client: httpx.AsyncClient
    ) -> None:
        """`/health` must not probe Postgres or Redis.

        If it did, a database blip would take the whole service out of the load
        balancer's rotation instead of merely marking it not ready. The
        separation is the point of having two endpoints.
        """
        response = await client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "checks" not in body

    async def test_readiness_reports_the_probe_result_without_raising(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unreachable database still answers 200 with the failure described.

        The precondition is set here rather than inherited: the suite's
        conftest points `DATABASE_URL` at an unroutable host, but that is a
        *default*, and running the whole suite against a real database — which
        Phase 26 does — replaced it. A test that depends on ambient environment
        passes in one run and fails in another for reasons that have nothing to
        do with the code. A probe that raises would turn a dependency outage
        into a 500 the caller cannot interpret.
        """
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.db import session as session_module

        # The engine, not the settings. `create_app()` runs at import time and
        # captured a `Settings` instance then, so patching a freshly-built one
        # leaves the running app untouched — and the suite's other suites change
        # the environment this test depends on.
        unreachable = create_async_engine(
            "postgresql+asyncpg://test:test@postgres.invalid:5432/none?ssl=disable"
        )
        monkeypatch.setattr(session_module, "get_engine", lambda: unreachable)
        monkeypatch.setattr(
            session_module,
            "get_session_factory",
            lambda: async_sessionmaker(unreachable, expire_on_commit=False),
        )
        try:
            response = await client.get("/ready")
        finally:
            await unreachable.dispose()
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "unavailable"
        assert body["checks"]["database"] == "unavailable"
        assert body["checks"]["error"]

    async def test_readiness_gates_on_pgvector(
        self, client: httpx.AsyncClient
    ) -> None:
        """A reachable database without pgvector is `degraded`, not `ok`.

        Similarity search and near-duplicate detection (D-006) silently produce
        nothing without the extension, so reporting `ok` would hide a real
        outage behind a green probe.
        """
        body = (await client.get("/ready")).json()
        assert body["status"] in {"ok", "degraded", "unavailable"}
        assert "pgvector" in body["checks"]


class TestDocsAndSpec:
    async def test_openapi_is_served(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]
        assert "/health" in paths
        assert "/ready" in paths

    async def test_logo_route_is_hidden_from_the_spec(
        self, client: httpx.AsyncClient
    ) -> None:
        """A binary asset in OpenAPI is noise for API consumers."""
        paths = (await client.get("/openapi.json")).json()["paths"]
        assert "/api/v1/brand/logo.png" not in paths
