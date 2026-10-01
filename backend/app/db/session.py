"""Database engine and session management (D-009).

Neon pooled endpoint over TLS. asyncpg driver.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        echo=settings.database_echo,
        # Neon pooled endpoints use a transaction-mode pooler. Disabling
        # server-side prepares avoids prepared-statement churn across the
        # pooler's ephemeral backend connections.
        connect_args={"server_settings": {"application_name": "catalogguard-api"}},
        pool_pre_ping=True,
    )


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Transactional scope for use outside request handlers."""
    session = get_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. Commits on success, rolls back on error."""
    async with session_scope() as session:
        yield session


async def check_connection() -> dict[str, Any]:
    """Verify connectivity and optional pgvector availability.

    Reported by /ready. Never raises: a probe failure is data, not an
    exception, so the health endpoint can surface it.
    """
    result: dict[str, Any] = {"database": "unavailable", "pgvector": "unavailable"}
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
            result["database"] = "ok"

            if get_settings().enable_pgvector:
                rows = await session.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
                version = rows.scalar()
                result["pgvector"] = "ok" if version else "not_installed"
                if version:
                    result["pgvector_version"] = version
    except Exception as exc:
        result["error"] = type(exc).__name__
    return result


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None