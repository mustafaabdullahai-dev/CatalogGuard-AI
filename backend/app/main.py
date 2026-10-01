"""ASGI application factory."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import configure_logging

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

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": settings.app_name,
            "environment": settings.environment,
        }

    @app.get("/ready", tags=["system"])
    async def ready() -> dict[str, object]:
        """Readiness probe. Component checks are added as they come online.

        Redis is reported as degraded, not fatal: the API can serve reads and
        enqueue intent before a worker exists (see D-009 endpoint status).
        """
        return {
            "status": "degraded",
            "checks": {
                "database": "unverified",
                "redis": "unavailable",
                "llm": "unverified",
                "pgvector": "unverified",
            },
            "note": "Component probes are wired in their respective phases.",
        }

    return app


app = create_app()