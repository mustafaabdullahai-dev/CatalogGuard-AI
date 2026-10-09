"""A real Postgres for the integration suite (Phase 24, §40).

Until this phase every claim in the repository rested on a mocked database, and
that gap was not theoretical: the first migration run against Postgres failed on
the HNSW index, and then on RLS never being enabled at all. Nothing in the unit
suite could have caught either.

The database is either one the environment already provides
(`CATALOGGUARD_TEST_DATABASE_URL`, which is what CI uses) or an ephemeral
`pgvector/pgvector:pg16` container started here and thrown away afterwards. No
testcontainer dependency: the lifecycle is four `docker` calls, and the
dependency list is a thing this project has chosen not to grow.

When neither is available the suite **skips**, and says so. A skipped suite is
an honest gap; a suite that silently passes against nothing is not.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base, TenantContext
from app.db.session import bind_tenant, clear_tenant

IMAGE = "pgvector/pgvector:pg16"
CONTAINER_NAME = "catalogguard-test-pg"
STARTUP_TIMEOUT_SECONDS = 60


@dataclass(frozen=True, slots=True)
class DatabaseHandle:
    url: str
    #: Whether this process started the container and must remove it.
    owned: bool
    container: str | None = None


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _start_container() -> DatabaseHandle:
    port = _free_port()
    subprocess.run(
        ["docker", "rm", "-f", CONTAINER_NAME],
        capture_output=True,
        check=False,
    )
    subprocess.run(
        [
            "docker", "run", "-d",
            "--name", CONTAINER_NAME,
            "-e", "POSTGRES_PASSWORD=catalogguard",
            "-e", "POSTGRES_DB=catalogguard_test",
            "-p", f"{port}:5432",
            IMAGE,
        ],
        capture_output=True,
        check=True,
        timeout=180,
    )
    url = (
        "postgresql+asyncpg://postgres:catalogguard"
        f"@127.0.0.1:{port}/catalogguard_test?ssl=disable"
    )
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        ready = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "pg_isready", "-U", "postgres"],
            capture_output=True,
            check=False,
        )
        # `pg_isready` is not enough on its own. The image runs initdb against a
        # temporary postmaster and then restarts, so readiness can be reported
        # seconds before the server the tests will actually use exists — and the
        # restart closes any connection made in between. The suite's first
        # symptom was "connection was closed in the middle of operation" from
        # the very first query. So the probe is a real connection doing a real
        # query, over the published port.
        if ready.returncode == 0 and _answers_a_query(url):
            return DatabaseHandle(url=url, owned=True, container=CONTAINER_NAME)
        time.sleep(1)
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True, check=False)
    raise RuntimeError(f"{IMAGE} did not become ready within {STARTUP_TIMEOUT_SECONDS}s")


def _answers_a_query(url: str) -> bool:
    """True when a full connect-and-query round trip succeeds."""

    async def _probe() -> bool:
        probe_engine = create_async_engine(url)
        try:
            async with probe_engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001 - a not-yet-ready server is the normal case
            return False
        finally:
            await probe_engine.dispose()

    try:
        return asyncio.run(_probe())
    except Exception:  # noqa: BLE001 - event loop or driver problems mean "not yet"
        return False


@pytest.fixture(scope="session")
def database() -> Iterator[DatabaseHandle]:
    """One database for the whole session, migrated once.

    Ordered so it starts after the unit suite's fakes are irrelevant: this is a
    real server, and a test that needs one must ask.
    """
    provided = os.getenv("CATALOGGUARD_TEST_DATABASE_URL")
    if provided:
        handle = DatabaseHandle(url=provided, owned=False)
    else:
        if not _docker_available():
            pytest.skip(
                "no CATALOGGUARD_TEST_DATABASE_URL and no usable Docker: the "
                "integration suite needs a real Postgres (D-006)"
            )
        handle = _start_container()
    try:
        yield handle
    finally:
        if handle.owned and handle.container:
            subprocess.run(
                ["docker", "rm", "-f", handle.container],
                capture_output=True,
                check=False,
            )


def _apply_migrations(url: str) -> None:
    """Run alembic to head against the test database.

    `-x allow_insecure=true` is the loopback-only escape `alembic/env.py` offers:
    TLS is mandatory for an online migration, and a container on 127.0.0.1 has
    nothing to encrypt for.
    """
    from alembic.config import Config

    _upgrade(Config("alembic.ini"), url)


def _upgrade(config: Any, url: str) -> None:
    from argparse import Namespace

    from alembic import command

    # `env.py` reads the URL from `-x url=`, and alembic exposes `-x` values as
    # `context.config.cmd_opts.x` — a parsed namespace, not a command-line
    # string. Passing a string leaves the attribute missing and the migration
    # never sees the URL.
    config.cmd_opts = Namespace(x=[f"url={url}", "allow_insecure=true"])
    command.upgrade(config, "head")


APP_ROLE = "catalogguard_app"
APP_PASSWORD = "catalogguard_app"


@pytest.fixture(scope="session")
def migrated(database: DatabaseHandle) -> str:
    os.environ.setdefault("DATABASE_URL", database.url)
    _apply_migrations(database.url)
    _create_app_role(database.url)
    return database.url


def _create_app_role(url: str) -> None:
    """A non-superuser role, because superusers bypass RLS entirely.

    `FORCE ROW LEVEL SECURITY` makes policies apply to a table's *owner*, which
    is the account the application uses in production. A superuser is exempt
    even from `FORCE` — so an isolation test run as `postgres` observes nothing
    and would happily pass while asserting the wrong thing. (This suite's first
    version did exactly that: the tenant-bound read returned another
    workspace's row and the assertion caught it.)

    The role is created here rather than in the migration because it is a test
    fixture, not part of the schema.
    """

    async def _create() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' "
                        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
                    )
                )
                # One statement per execute: asyncpg refuses a multi-command
                # prepared statement, which is the same rule the migration
                # tripped over.
                for statement in (
                    f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                    f"IN SCHEMA public TO {APP_ROLE}",
                    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
                    f"TO {APP_ROLE}",
                ):
                    await connection.execute(text(statement))
        finally:
            await engine.dispose()

    try:
        asyncio.run(_create())
    except Exception as exc:  # noqa: BLE001 - a pre-existing role is fine
        if "already exists" not in str(exc):
            raise


def app_url(url: str) -> str:
    """The same database, connected as the application role rather than as root."""
    _, _, rest = url.partition("://")
    _, _, hostpart = rest.partition("@")
    return url.replace(
        f"postgres:catalogguard@{hostpart}",
        f"{APP_ROLE}:{APP_PASSWORD}@{hostpart}",
        1,
    )


@pytest_asyncio.fixture
async def engine(migrated: str) -> AsyncIterator[Any]:
    test_engine = create_async_engine(migrated, pool_pre_ping=True)
    try:
        yield test_engine
    finally:
        await test_engine.dispose()



@pytest_asyncio.fixture
async def app_engine(migrated: str) -> AsyncIterator[Any]:
    """An engine owned by the non-superuser application role.

    Every RLS assertion needs this one: as a superuser the policies do not apply,
    and a test that "passes" proves nothing.
    """
    test_engine = create_async_engine(app_url(migrated), pool_pre_ping=True)
    try:
        yield test_engine
    finally:
        await test_engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: Any) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(
    engine: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A session on an empty schema.

    Every test starts from nothing: truncating rather than rolling back is
    deliberate, because a rolled-back fixture would hide exactly the commit-time
    behaviour (RLS `SET LOCAL`, triggers) these tests exist to observe.
    """
    # `CASCADE` belongs to the end of the whole list, not to each table: one
    # TRUNCATE with a comma-separated list and a single trailing CASCADE.
    names = ", ".join(
        f'"{row.name}"' for row in reversed(Base.metadata.sorted_tables)
    )
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {names} CASCADE"))
    async with session_factory() as session:
        yield session


@asynccontextmanager
async def tenant_session(
    factory: async_sessionmaker[AsyncSession],
    context: TenantContext,
) -> AsyncIterator[AsyncSession]:
    """A session with the tenant bound, the way a request has it.

    `SET LOCAL` is transaction-scoped: binding is required *inside* every
    transaction, which is why `get_current_tenant` binds per request rather than
    per connection. Using this helper makes each block in a test a transaction
    that is bound the way the application's would be — and a test that forgets
    to bind fails the same way a request that forgets to would, rather than
    passing against a connection that happens to still carry a tenant.
    """

    async with factory() as session:
        await bind_tenant(session, context.workspace_id)
        try:
            yield session
        finally:
            await session.rollback()


async def make_catalog(
    session: AsyncSession,
    context: TenantContext,
    *,
    name: str = "Integration catalog",
) -> Any:
    """A real catalog row: products carry a composite FK to one.

    Inventing a catalog id fails on the database, which is the suite doing its
    job — the ORM would not have told us.
    """
    from app.db.session import bind_tenant
    from app.models.catalog import Catalog

    await bind_tenant(session, context.workspace_id)
    catalog = Catalog(
        workspace_id=context.workspace_id,
        name=name,
        slug=f"catalog-{uuid4().hex[:8]}",
    )
    session.add(catalog)
    # Flushed, not committed: `SET LOCAL` is transaction-scoped, so committing
    # here would silently drop the tenant binding and the caller's next write
    # would be refused by the very policy this suite exists to test.
    await session.flush()
    return catalog


@pytest.fixture
def workspace() -> TenantContext:
    """A tenant for one test. Its own UUID, so truncation between tests is safe."""
    return TenantContext(workspace_id=uuid4(), user_id=uuid4())


@pytest.fixture
def other_workspace() -> TenantContext:
    """A second tenant: the one an isolation test must not be able to read."""
    return TenantContext(workspace_id=uuid4(), user_id=uuid4())


def tenant(user_id: Any = None) -> TenantContext:
    return TenantContext(workspace_id=uuid4(), user_id=user_id or uuid4())


async def bound_session(
    session_factory: async_sessionmaker[AsyncSession],
    context: TenantContext,
) -> AsyncIterator[AsyncSession]:
    """A session with the tenant bound, as a request would have it."""
    async with session_factory() as session:
        await bind_tenant(session, context.workspace_id)
        yield session


@pytest_asyncio.fixture
async def unbound_session(session_factory: async_sessionmaker[AsyncSession]) -> AsyncSession:
    """A session with no tenant bound: RLS must return nothing."""
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def app_session(
    engine: Any,
    app_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A session as the application role, on an empty schema.

    The truncation runs on the admin engine: `TRUNCATE` is a table-owner
    privilege, and the application role has DML, which is all it needs in
    production.
    """
    names = ", ".join(f'"{row.name}"' for row in reversed(Base.metadata.sorted_tables))
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {names} CASCADE"))
    async with app_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def app_session_factory(app_engine: Any) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(app_engine, expire_on_commit=False)


__all__ = [
    "app_session",
    "other_workspace",
    "workspace",
    "tenant_session",
    "app_session_factory",
    "app_url",
    "bound_session",
    "make_catalog",
    "clear_tenant",
    "db_session",
    "engine",
    "migrated",
    "session_factory",
    "tenant",
    "unbound_session",
]