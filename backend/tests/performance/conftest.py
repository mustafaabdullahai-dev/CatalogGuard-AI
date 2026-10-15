"""Performance measurement against a real database (Phase 27, §57).

§57's rules are reviewable claims until something counts: "must not be driven by
per-row queries", "large catalogs must not be loaded entirely into frontend
memory". A reviewer reading `list_catalog_sources` sees one grouped count and
believes it. This suite measures instead.

**What is asserted is the shape, not the milliseconds.** Latency numbers depend
on the machine; a budget in milliseconds would either flake or be so loose it
means nothing. What must hold on every machine is:

- the number of statements an operation issues does not grow with the number of
  rows it returns — that is the N+1 rule, asserted as an invariant across two
  row counts rather than as a count in the abstract;
- the planner uses an index on the hot filter columns, checked with `EXPLAIN`
  on a table large enough for the choice to matter;
- latency stays inside a budget recorded per operation, so a regression shows up
  as a failed number rather than as a feeling.

Measurements are printed as well as asserted. The numbers are the deliverable;
the thresholds are a guard rail.

No dependency is added: statement counting is a SQLAlchemy event listener and
`EXPLAIN` is a query. `locust` and `pytest-benchmark` would add a load generator
this project does not need yet — Phase 28 measures a deployed container, which
is the environment where these numbers actually matter.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest_asyncio
from sqlalchemy import event, insert, text
from sqlalchemy.ext.asyncio import AsyncSession

# Re-exported rather than reimplemented, for the same reason as
# `tests/security`: these fixtures are already proven, and `pytest_plugins` is
# only legal in the rootdir conftest.
from tests.integration import conftest as _integration

app_engine = _integration.app_engine
app_session = _integration.app_session
app_session_factory = _integration.app_session_factory
database = _integration.database
db_session = _integration.db_session
engine = _integration.engine
migrated = _integration.migrated
session_factory = _integration.session_factory
tenant_session = _integration.tenant_session

#: Row counts for the two scale points. Small enough that any N+1 is obvious,
#: large enough that a sequential scan on an unindexed column costs real time.
SMALL = 25
LARGE = 2_000


@dataclass
class StatementLog:
    """Every statement the session issued, in order."""

    statements: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.statements)

    def counts_by_shape(self) -> dict[str, int]:
        """Statements grouped by their first line, for reading a profile."""

        shapes: dict[str, int] = {}
        for statement in self.statements:
            head = " ".join(statement.split())[:90]
            shapes[head] = shapes.get(head, 0) + 1
        return shapes


@pytest_asyncio.fixture
async def statement_log(app_session: AsyncSession) -> Any:
    """Count statements issued while the test runs.

    Attached to the engine the session belongs to, so the count is complete
    rather than whatever the test remembered to wrap. `set_config` for the tenant
    binding is excluded: it is one statement per transaction by design, and it
    would otherwise make a per-operation budget look one higher than it is.
    """
    log = StatementLog()

    def _record(conn, cursor, statement, parameters, context, executemany):
        if "set_config" in statement:
            return
        log.statements.append(statement)

    event.listen(app_session.bind.sync_engine, "before_cursor_execute", _record)
    try:
        yield log
    finally:
        event.remove(app_session.bind.sync_engine, "before_cursor_execute", _record)


async def _truncate_catalogs(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE simulation_runs, approval_actions, approval_requests, "
                "risk_assessments, change_proposals, conflicts, reconciliation_runs, "
                "source_records, catalog_sources, products, catalogs CASCADE"
            )
        )


async def build_catalog(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    products: int = SMALL,
    sources: int = 2,
    records_per_product: int = 2,
) -> dict[str, Any]:
    """A workspace holding `products` products and their source records.

    Built with bulk inserts rather than ORM adds: seeding ten thousand rows one
    instance at a time would dominate the suite's runtime, and a performance
    suite that is slow to set up is one people stop running.
    """
    from app.models.catalog import Catalog

    catalog_id = uuid.uuid4()
    session.add(
        Catalog(
            id=catalog_id,
            workspace_id=workspace_id,
            name="Performance",
            # Unique per build: the slug is unique per workspace, and one test
            # rebuilds the same workspace twice.
            slug=f"perf-{catalog_id.hex[:8]}",
        )
    )
    await session.flush()

    source_ids = [uuid.uuid4() for _ in range(sources)]
    for index, source_id in enumerate(source_ids):
        await session.execute(
            text(
                "INSERT INTO catalog_sources (id, workspace_id, name, source_type, "
                "detected_schema, authorization_context) VALUES "
                "(:id, :ws, :name, :source_type, '{}'::jsonb, '{}'::jsonb)"
            ),
            {
                "id": source_id,
                "ws": workspace_id,
                "name": f"Supplier {index}",
                "source_type": "manufacturer" if index == 0 else "internal",
            },
        )

    product_rows = [
        {
            "id": uuid.uuid4(),
            "workspace_id": workspace_id,
            "catalog_id": catalog_id,
            "sku": f"PERF-{index:07d}",
            "brand": "Northwind" if index % 2 == 0 else "Nordvik",
            "title": f"Performance product {index}",
            "category_path": ["outerwear", "footwear", "packs"][index % 3],
            "status": "active",
            "version": 1,
            "attributes": {"color": ["navy", "slate", "olive"][index % 3]},
        }
        for index in range(products)
    ]
    for start in range(0, len(product_rows), 500):
        await session.execute(
            insert(_table("products")),
            product_rows[start : start + 500],
        )

    product_ids = list(await _product_ids(session, workspace_id, products))
    record_rows = [
        {
            "id": uuid.uuid4(),
            "workspace_id": workspace_id,
            "catalog_source_id": source_ids[index % sources],
            "external_id": f"E-{index:07d}",
            # Linked to a product: reconciliation only builds observations for
            # records it can attach, and an unlinked corpus profiles nothing.
            "product_id": product_ids[index % len(product_ids)],
            "raw_data": {
                "sku": f"PERF-{index:07d}",
                "title": f"Performance product {index}",
                "color": ["navy", "slate", "olive"][index % 3],
                "price": f"{100 + (index % 400)}.00",
            },
        }
        for index in range(len(product_ids) * records_per_product)
    ]
    for start in range(0, len(record_rows), 500):
        await session.execute(
            insert(_table("source_records")),
            record_rows[start : start + 500],
        )

    await session.commit()
    return {
        "catalog_id": catalog_id,
        "source_ids": source_ids,
        "product_ids": product_ids,
        "products": products,
        "records": len(record_rows),
    }


async def _product_ids(session: AsyncSession, workspace_id: uuid.UUID, limit: int):
    """The ids just inserted, read back so the fixture can attach records."""

    result = await session.execute(
        text(
            "SELECT id FROM products WHERE workspace_id = :ws "
            "ORDER BY created_at DESC LIMIT :n"
        ),
        {"ws": workspace_id, "n": limit},
    )
    return list(result.scalars())


def _table(name: str):
    from app.db.base import Base

    return Base.metadata.tables[name]


@pytest_asyncio.fixture
async def perf_identity(engine: Any):
    """One organization, workspace and member, recreated for each test.

    Function-scoped on purpose. A session-scoped fixture cannot depend on the
    integration suite's function-scoped engine, and rebuilding tenancy is four
    inserts — cheaper than the reasoning a shared identity would need.
    """
    from app.core.security.passwords import hash_password
    from app.models.tenancy import UserRole

    await _truncate_catalogs(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE workspace_memberships, auth_sessions, users, workspaces, "
                "organizations, login_throttles CASCADE"
            )
        )

    organization, workspace, user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug, is_active) "
                "VALUES (:id, 'Performance', :slug, true)"
            ),
            {"id": organization, "slug": f"perf-org-{organization.hex[:8]}"},
        )
        await connection.execute(
            text(
                "INSERT INTO workspaces (id, organization_id, name, slug) "
                "VALUES (:id, :org, 'Performance', :slug)"
            ),
            {"id": workspace, "org": organization, "slug": f"perf-{workspace.hex[:8]}"},
        )
        await connection.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, full_name, "
                "is_active, is_superuser) VALUES (:id, 'perf@example.com', :hash, "
                "'Perf', true, false)"
            ),
            {"id": user, "hash": hash_password("correct horse battery staple")},
        )
        await connection.execute(
            text(
                "INSERT INTO workspace_memberships (id, workspace_id, user_id, "
                "role, created_at, updated_at) VALUES (:id, :ws, :user, :role, "
                "now(), now())"
            ),
            {
                "id": uuid.uuid4(),
                "ws": workspace,
                "user": user,
                "role": UserRole.ECOMMERCE_OPS_MANAGER.value,
            },
        )

    try:
        yield {"organization": organization, "workspace": workspace, "user": user}
    finally:
        await _truncate_catalogs(engine)
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE workspace_memberships, auth_sessions, users, "
                    "workspaces, organizations CASCADE"
                )
            )


def async_session_factory_session(engine: Any):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(engine, expire_on_commit=False)()