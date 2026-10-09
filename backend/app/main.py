"""ASGI application factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import (
    approvals,
    auth,
    brand,
    catalog,
    compliance,
    copilot,
    ingestion,
    invitations,
    observability,
    reconciliation,
)
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.middleware import (
    CSRFMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
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
    # Added first, so it wraps the rest: the request id and timing must be
    # bound for every response, including the ones CSRF refuses.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(CSRFMiddleware, settings=settings)

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    app.include_router(brand.router, prefix=settings.api_prefix)
    app.include_router(auth.router, prefix=settings.api_prefix)
    app.include_router(invitations.router, prefix=settings.api_prefix)
    app.include_router(ingestion.router, prefix=settings.api_prefix)
    app.include_router(catalog.router, prefix=settings.api_prefix)
    app.include_router(reconciliation.router, prefix=settings.api_prefix)
    app.include_router(compliance.router, prefix=settings.api_prefix)
    app.include_router(approvals.router, prefix=settings.api_prefix)
    app.include_router(copilot.router, prefix=settings.api_prefix)
    app.include_router(observability.router, prefix=settings.api_prefix)

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

    _install_openapi_security(app, settings)
    return app


# Everything else in this API needs the session cookie, and the spec now says so:
# Phase 24's contract test found the served OpenAPI document declared no
# authentication at all, so `/docs` described an API that looked anonymous.
def _public_operations(prefix: str) -> frozenset[tuple[str, str]]:
    """Routes a client calls before it has a session."""

    return frozenset(
        {
            ("get", "/health"),
            ("get", "/ready"),
            ("get", "/openapi.json"),
            ("get", "/docs"),
            ("get", f"{prefix}/brand/manifest"),
            ("post", f"{prefix}/auth/login"),
            ("get", f"{prefix}/auth/csrf"),
            ("post", f"{prefix}/auth/password-reset"),
            ("post", f"{prefix}/auth/password-reset/confirm"),
            ("post", f"{prefix}/auth/invitations/accept"),
        }
    )


HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def _install_openapi_security(app: FastAPI, settings: Settings) -> None:
    """Declare the session cookie as the API's security scheme.

    Authentication is a session cookie read by a dependency, which FastAPI
    cannot infer. Without this the generated spec lists forty operations and
    says none of them need a credential — the reverse of the truth, and the kind
    of error a reader of `/docs` acts on.
    """
    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schema.setdefault("components", {})["securitySchemes"] = {
            "sessionCookie": {
                "type": "apiKey",
                "in": "cookie",
                "name": settings.session_cookie_name,
                "description": (
                    "Issued by `POST /api/v1/auth/login`. Mutations also require "
                    "the `X-CSRF-Token` header matching the `"
                    f"{settings.csrf_cookie_name}` cookie."
                ),
            }
        }
        public = _public_operations(settings.api_prefix)
        for path, item in schema.get("paths", {}).items():
            for method, operation in item.items():
                if method in HTTP_METHODS and (method, path) not in public:
                    operation["security"] = [{"sessionCookie": []}]

        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


app = create_app()