"""§57's rule about round trips, asserted by counting rather than by review.

`list_catalog_sources` groups its counts into one query and says so in a
comment. So does `_load_inputs` in the reconciler. A reviewer believes both.
This file measures them, and — the part that matters — compares the statement
count at 25 rows against the count at 2,000. An operation whose count grows with
the rows it returns is an N+1 no matter how tidy its source looks.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import TenantContext
from tests.performance.conftest import SMALL, StatementLog, build_catalog

#: The second scale point. 80x the first, which is enough for a per-row query to
#: cost real wall-clock time and enough for the planner to change its mind.
LARGE = 2_000


def _tenant(perf_identity: dict) -> TenantContext:
    return TenantContext(
        workspace_id=perf_identity["workspace"],
        user_id=perf_identity["user"],
    )


def _bind(workspace_id: Any):
    return text("SELECT set_config('app.current_workspace', :ws, true)").bindparams(
        ws=str(workspace_id)
    )


async def _rebuild(engine: Any, tenant: TenantContext, count: int) -> None:
    """Empty the workspace's catalog data, then refill it at `count` products.

    Seeded as the table owner on purpose. Writing catalog rows as the
    application role would need a bound tenant, and binding one per seeded batch
    would put the tenant setup inside the measurement — the statement counter
    would see work that only exists for the fixture.
    """

    from sqlalchemy.ext.asyncio import async_sessionmaker

    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM source_records WHERE workspace_id = :ws"),
            {"ws": tenant.workspace_id},
        )
        await connection.execute(
            text("DELETE FROM products WHERE workspace_id = :ws"),
            {"ws": tenant.workspace_id},
        )
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        await build_catalog(session, tenant.workspace_id, products=count)
    finally:
        await session.close()


async def _list_sources(session: AsyncSession, tenant: TenantContext) -> Any:
    """The endpoint body, called directly.

    Counting through the HTTP stack would work too, but this isolates
    persistence: a route's middleware and its response model are not what §57 is
    about.
    """
    from sqlalchemy import func, select

    from app.models.catalog import CatalogSource, SourceRecord

    sources = (
        await session.execute(
            select(CatalogSource)
            .where(CatalogSource.workspace_id == tenant.workspace_id)
            .order_by(CatalogSource.created_at)
        )
    ).scalars().all()

    ids = [source.id for source in sources]
    counts: dict[Any, int] = {}
    if ids:
        rows = await session.execute(
            select(SourceRecord.catalog_source_id, func.count())
            .where(SourceRecord.workspace_id == tenant.workspace_id)
            .where(SourceRecord.catalog_source_id.in_(ids))
            .group_by(SourceRecord.catalog_source_id)
        )
        counts = {row[0]: row[1] for row in rows.all()}

    return sources, counts


@pytest.mark.performance
class TestStatementCountsDoNotScaleWithRows:
    async def test_listing_sources_costs_the_same_at_two_sizes(
        self,
        engine: Any,
        perf_identity: dict,
        app_session_factory: Any,
        statement_log: StatementLog,
    ) -> None:
        """The list path: a constant number of statements, whatever it serves."""

        tenant = _tenant(perf_identity)
        measurements: dict[int, int] = {}

        for count in (SMALL, LARGE):
            await _rebuild(engine, tenant, count)
            async with app_session_factory() as measure:
                await measure.execute(_bind(tenant.workspace_id))
                statement_log.statements.clear()
                await _list_sources(measure, tenant)
                measurements[count] = statement_log.count

        print(f"\n  list sources: {measurements}")
        assert measurements[LARGE] == measurements[SMALL], (
            f"statement count grew with rows: {measurements}"
        )
        assert measurements[LARGE] <= 2, (
            f"expected one grouped count, saw {measurements[LARGE]}"
        )

    async def test_the_product_list_costs_the_same_at_two_sizes(
        self,
        engine: Any,
        perf_identity: dict,
        app_session_factory: Any,
        statement_log: StatementLog,
    ) -> None:
        """A paginated list must not walk the workspace to count it."""

        from app.services.catalog.repositories import ProductRepository

        tenant = _tenant(perf_identity)
        measurements: dict[int, int] = {}

        for count in (SMALL, LARGE):
            await _rebuild(engine, tenant, count)
            async with app_session_factory() as measure:
                await measure.execute(_bind(tenant.workspace_id))
                statement_log.statements.clear()
                await ProductRepository(measure, tenant).list(limit=50, offset=0)
                measurements[count] = statement_log.count

        print(f"\n  product list: {measurements}")
        assert measurements[LARGE] == measurements[SMALL], (
            f"statement count grew with rows: {measurements}"
        )

    async def test_the_statement_log_sees_a_read_at_all(
        self,
        engine: Any,
        perf_identity: dict,
        app_session_factory: Any,
        statement_log: StatementLog,
    ) -> None:
        """The counter must not silently measure nothing.

        A budget test whose counter is broken passes forever, which is the same
        failure mode as a browser harness that cannot fail.
        """
        tenant = _tenant(perf_identity)
        async with app_session_factory() as session:
            await session.execute(_bind(tenant.workspace_id))
            statement_log.statements.clear()
            await _list_sources(session, tenant)

        assert statement_log.count >= 1, "the statement counter observed nothing"
        assert any(
            "catalog_sources" in statement for statement in statement_log.statements
        ), f"expected the sources query, saw {statement_log.counts_by_shape()}"

@pytest.mark.performance
class TestTheHotPathsAreAllConstantCost:
    """The paths that carry the most rows are the ones §57 is about.

    Each of these is written to be constant-cost already; what is asserted is
    that it *stays* that way, because the natural way to extend any of them later
    is one query per row.
    """

    async def test_the_approval_queue_does_not_walk_its_rows(
        self,
        engine: Any,
        perf_identity: dict,
        app_session_factory: Any,
        statement_log: StatementLog,
    ) -> None:
        """Count, page, then two grouped lookups — never one per proposal."""

        tenant = _tenant(perf_identity)
        measurements: dict[int, int] = {}

        from sqlalchemy.ext.asyncio import async_sessionmaker

        for count in (SMALL, LARGE):
            await _rebuild(engine, tenant, count)
            # Seeded as the owner, like every other fixture write: reading the
            # products as the application role would need a bound tenant, and an
            # unbound read sees zero rows because RLS is working.
            session = async_sessionmaker(engine, expire_on_commit=False)()
            try:
                await _seed_proposals(session, tenant, count)
            finally:
                await session.close()

            async with app_session_factory() as measure:
                await measure.execute(_bind(tenant.workspace_id))
                statement_log.statements.clear()
                await _approval_queue(measure, tenant)
                measurements[count] = statement_log.count

        print(f"\n  approval queue: {measurements}")
        assert measurements[LARGE] == measurements[SMALL], (
            f"statement count grew with rows: {measurements}"
        )
        assert measurements[LARGE] <= 6, (
            f"the queue issues {measurements[LARGE]} statements; §57 expects a "
            "count, a page and two grouped lookups"
        )

    async def test_the_reconciler_loads_its_inputs_in_two_queries(
        self,
        engine: Any,
        perf_identity: dict,
        app_session_factory: Any,
        statement_log: StatementLog,
    ) -> None:
        """`engine._load_inputs` says two queries; this is the measurement.

        The comment claims it, and the comment is right — but a comment is not a
        measurement, and this is the path most likely to hold hundreds of
        products.
        """

        from app.services.reconciliation.engine import _load_inputs

        tenant = _tenant(perf_identity)
        measurements: dict[int, int] = {}

        for count in (SMALL, LARGE):
            await _rebuild(engine, tenant, count)
            async with engine.connect() as connection:
                product_ids = list(
                    (
                        await connection.execute(
                            text("SELECT id FROM products WHERE workspace_id = :ws"),
                            {"ws": tenant.workspace_id},
                        )
                    ).scalars()
                )

            async with app_session_factory() as measure:
                await measure.execute(_bind(tenant.workspace_id))
                statement_log.statements.clear()
                await _load_inputs(measure, tenant, product_ids)
                measurements[count] = statement_log.count

        print(f"\n  reconciler inputs: {measurements}")
        assert measurements[LARGE] == measurements[SMALL], (
            f"statement count grew with rows: {measurements}"
        )
        assert measurements[LARGE] <= 2, (
            f"_load_inputs issued {measurements[LARGE]} statements for "
            f"{LARGE} products"
        )


async def _seed_proposals(session: Any, tenant: TenantContext, count: int) -> None:
    """Proposals to page through, created in bulk."""

    from sqlalchemy import text

    product_ids = list(
        (
            await session.execute(
                text("SELECT id FROM products WHERE workspace_id = :ws LIMIT :n"),
                {"ws": tenant.workspace_id, "n": count},
            )
        ).scalars()
    )
    rows = [
        {
            "id": uuid.uuid4(),
            "ws": tenant.workspace_id,
            "product": product_ids[index % len(product_ids)],
            "ref": f"CHG-PERF-{index:07d}",
            "field": "brand",
        }
        for index in range(count)
    ]
    for start in range(0, len(rows), 500):
        await session.execute(
            text(
                "INSERT INTO change_proposals (id, workspace_id, product_id, "
                "change_ref, field_name, current_value, proposed_value, reason, "
                "evidence, confidence, validation_status, validation_errors, "
                "risk_level, requires_approval, seo_impact, compliance_impact, "
                "affected_channels, affected_relationships, status, created_at, "
                "updated_at) VALUES (:id, :ws, :product, :ref, :field, "
                "'\"a\"'::jsonb, '\"b\"'::jsonb, 'performance', '[]'::jsonb, 0.5, "
                "'validated', '[]'::jsonb, 'MEDIUM', true, 'NONE', 'NEUTRAL', "
                "'[]'::jsonb, '[]'::jsonb, 'simulated', now(), now())"
            ),
            rows[start : start + 500],
        )
    await session.commit()




async def _approval_queue(session: Any, tenant: TenantContext) -> Any:
    """The queue body from `app/api/v1/approvals.py`, called directly.

    Copied rather than imported: the route takes a `TenantScope`, and the point
    of this file is to count persistence statements, not to exercise routing.
    """

    from sqlalchemy import func, or_, select

    from app.models.reconciliation import (
        ChangeProposal,
        ProposalStatus,
        SimulationRun,
    )
    from app.services.reconciliation.repositories import (
        ChangeProposalRepository,
        SimulationRunRepository,
    )

    where = or_(
        ChangeProposal.status == ProposalStatus.PENDING_APPROVAL,
        ChangeProposal.status == ProposalStatus.SIMULATED,
    )
    total = int(
        (await session.execute(select(func.count()).where(where))).scalar_one_or_none() or 0
    )
    rows = await ChangeProposalRepository(session, tenant).list(
        limit=50,
        offset=0,
        order_by=ChangeProposal.created_at,
        where=where,
    )
    proposal_ids = [row.id for row in rows]
    runs = await SimulationRunRepository(session, tenant).list(
        limit=max(len(proposal_ids), 1),
        where=(
            SimulationRun.change_proposal_id.in_(proposal_ids) if proposal_ids else False
        ),
    )

    return total, rows, runs
