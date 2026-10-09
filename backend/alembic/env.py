"""Alembic environment.

Async-aware: reads DATABASE_URL through the app settings layer so a plaintext
connection string is rejected before Alembic ever reaches the database.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings_or_none
from app.db.base import Base
from app.models import *  # noqa: F403 - registers all tables on Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _is_loopback(url: str) -> bool:
    host = url.split("://", 1)[-1].split("/", 1)[0].rsplit("@", 1)[-1].split(":")[0]
    return host in ("127.0.0.1", "localhost", "::1", "[::1]")


def get_url() -> str:
    """Connection string with the async driver.

    Resolution order:
      1. `-x url=...` on the Alembic command line.
      2. Validated `Settings.database_url`.
      3. A raw `DATABASE_URL` from the environment.

    Step 3 exists because `Settings` rejects placeholder secrets, which would
    otherwise make `alembic upgrade --sql` impossible to run before credentials
    are provisioned. Offline SQL generation never opens a connection, so an
    unvalidated string is safe there; online mode has no such escape hatch and
    still fails closed.
    """
    override = context.get_x_argument(as_dictionary=True).get("url")
    if override:
        url = override
    else:
        settings = get_settings_or_none()
        if settings is not None:
            url = settings.database_url
        else:
            url = os.environ.get("DATABASE_URL") or config.get_main_option(
                "sqlalchemy.url", ""
            )

    if not url:
        raise RuntimeError(
            "No database URL available. Check .env has DATABASE_URL set with "
            "sslmode=require, or pass -x url=postgresql://..."
        )

    if context.is_offline_mode():
        return url

    # Online migrations actually connect, so TLS is mandatory. Enforced here
    # rather than trusting validation that may have been skipped above.
    #
    # One explicit exception, for the integration suite (Phase 24): an ephemeral
    # Postgres on loopback, where no byte leaves the host and asyncpg only
    # accepts `ssl=` rather than `sslmode=`. It requires both the `-x
    # allow_insecure=true` flag and a loopback host, so the rule still fails
    # closed for any URL a deployment could actually use.
    insecure_allowed = (
        context.get_x_argument(as_dictionary=True).get("allow_insecure") == "true"
        and _is_loopback(url)
    )
    if "sslmode=" not in url and not insecure_allowed:
        raise RuntimeError(
            "DATABASE_URL must set sslmode=require for an online migration."
        )

    # Alembic's async engine requires the asyncpg driver scheme.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


def run_migrations_offline() -> None:
    """Emit SQL without connecting. Used to review a migration before applying."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()