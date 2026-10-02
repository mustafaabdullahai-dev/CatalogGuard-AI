"""ASGI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import auth, brand, invitations
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.middleware import CSRFMiddleware, SecurityHeadersMiddleware
from app.db.session import check_connection

DESCRIPTION = """
CatalogGuard AI — catalog reconciliation and change-safety platform.

The LLM proposes. Deterministic validation decides. Rules constrain.
The sandbox simulates. Humans approve. Connectors execute.
"""


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()

    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Added last, so they run first. CSRF has to see the request before routing
    # decides anything, and the headers have to be on the way out whatever the
    # outcome.
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(CSRFMiddleware, settings=settings)

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    app.include_router(brand.router, prefix=settings.api_prefix)
    app.include_router(auth.router, prefix=settings.api_prefix)
    app.include_router(invitations.router, prefix=settings.api_prefix)

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        path = static_dir / "logo.png"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(path, media_type="image/png")

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": settings.app_name,
            "environment": settings.environment,
        }

    @app.get("/ready", tags=["system"])
    async def ready() -> dict[str, object]:
        """Readiness probe.

        Reports rather than raises: a failing probe is the answer this endpoint
        exists to give, and raising would turn a dependency outage into a
        500 that a load balancer cannot interpret.

        Redis is not fatal. The API serves reads and enqueues intent before a
        worker exists (see D-009 endpoint status), so an unreachable Redis is
        `degraded`, not `unavailable`. Postgres and pgvector both gate real
        traffic, so either one failing is `unavailable`.
        """
        database = await check_connection()
        checks: dict[str, str] = {
            "database": str(database.get("database", "unavailable")),
            "pgvector": str(database.get("pgvector", "unverified")),
            "redis": "unverified",
        }
        if "error" in database:
            checks["error"] = str(database["error"])

        if checks["database"] == "ok" and checks["pgvector"] == "ok":
            status = "ok"
        elif checks["database"] == "ok":
            status = "degraded"
        else:
            status = "unavailable"

        return {
            "status": status,
            "checks": checks,
            "note": (
                "pgvector is required for similarity search and near-duplicate "
                "detection (D-006). Redis is not probed here; the API can enqueue "
                "intent without a live worker."
            ),
        }

    return app


app = create_app()