"""Database engine and session management (D-009).

Neon pooled endpoint over TLS. asyncpg driver.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Imported for the side effect of registering every model on the shared
# metadata. There is deliberately no second DeclarativeBase here: a second
# base means a second empty MetaData, and Alembic autogenerate would then see
# an empty schema and propose dropping every table.
import app.models  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base  # noqa: F401  (re-exported for callers)
from app.db.rls import bind_tenant_statement, bind_user_statement, clear_tenant_statement

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


async def bind_tenant(session: AsyncSession, workspace_id: UUID | str) -> None:
    """Bind the current transaction to one workspace for RLS (D-011).

    Must run inside an open transaction, which `session_scope` provides. The
    value is `SET LOCAL`, so it disappears at commit or rollback and a pooled
    connection cannot carry a tenant into a later request.

    Phase 8 wires this to the authenticated principal. Until then nothing
    calls it except the tests, and RLS therefore denies every tenant query --
    fail-closed rather than fail-open, which is the point of shipping the
    policy before the identity layer.
    """
    await session.execute(bind_tenant_statement(workspace_id))


async def bind_user(session: AsyncSession, user_id: UUID | str) -> None:
    """Bind the current transaction to one user, the way `bind_tenant` binds a workspace.

    Needed before a workspace is known: session resolution reads the user's
    memberships to find out which workspaces they have. Without this, RLS
    returns nothing and every signed-in user resolves to zero workspaces
    (D-011, Phase 24). Call it with the id of the user the session resolved
    to, never with one a caller supplied.
    """
    await session.execute(bind_user_statement(user_id))


async def clear_tenant(session: AsyncSession) -> None:
    """Clear the tenant context inside the current transaction.

    For privileged work in a transaction that also carries tenant reads. A
    cleared tenant cannot read or write tenant rows at all.
    """
    await session.execute(clear_tenant_statement())


@asynccontextmanager
async def tenant_scope(
    workspace_id: UUID | str,
) -> AsyncGenerator[AsyncSession, None]:
    """Transactional scope with one workspace already bound.

    The shape background jobs and agents should use, so that a worker cannot
    accidentally query tenant data without an explicit tenant:

        async with tenant_scope(workspace.id) as session:
            ...
    """
    async with session_scope() as session:
        await bind_tenant(session, workspace_id)
        yield session


@asynccontextmanager
async def manual_session() -> AsyncGenerator[AsyncSession, None]:
    """Session with no automatic commit.

    For flows that must persist something on the failure path. Sign-in is the
    example: a wrong password still has to increment the throttle counters, so
    the failure cannot be allowed to roll the transaction back.
    """
    session = get_session_factory()()
    try:
        yield session
    finally:
        await session.close()


async def get_session_no_commit() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency wrapping `manual_session`.

    A generator function rather than the context manager itself, because
    FastAPI inspects the dependency for `yield` and an `@asynccontextmanager`
    wrapper does not present as one.
    """
    async with manual_session() as session:
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