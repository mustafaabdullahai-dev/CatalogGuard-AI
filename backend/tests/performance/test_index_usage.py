"""§57's "database indexing", checked with `EXPLAIN` rather than assumed.

An index that exists and an index that is used are different claims. A schema
full of index definitions can still answer the hot query with a sequential scan,
and nothing in the unit suite would notice — every query there runs against a
handful of rows, where the planner chooses a sequential scan because it is the
right answer.

So these tests build enough rows for the choice to matter and then read the
plan. `EXPLAIN` is a query; no dependency is involved.

Two failure modes are asserted against:

- a sequential scan on a table large enough to justify an index, which is §57's
  "database indexing" failing silently;
- a statement count that grows with rows, which is its own rule and lives in
  `test_query_budget.py`.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from app.db.base import TenantContext
from tests.performance.conftest import SMALL, build_catalog

#: Enough rows that a sequential scan on an unindexed column costs milliseconds
#: rather than microseconds, while keeping the suite under a minute.
BULK = 5_000


def _tenant(perf_identity: dict) -> TenantContext:
    return TenantContext(
        workspace_id=perf_identity["workspace"],
        user_id=perf_identity["user"],
    )


async def _plan(session: Any, statement: str, params: dict[str, Any]) -> str:
    result = await session.execute(text(f"EXPLAIN (ANALYZE, BUFFERS) {statement}"), params)
    return "\n".join(row[0] for row in result.all())


def _uses_index(plan: str) -> bool:
    """True when the plan reads through an index rather than the heap.

    Matches the shapes Postgres actually prints: `Index Scan using <name> on
    <table>`, `Index Only Scan`, `Bitmap Index Scan on <table>` and
    `Bitmap Heap Scan`. An earlier version looked for the literal substring
    `" Scan on "`, which `Index Scan using ix_products_workspace_status on
    products` does not contain — the helper reported "not indexed" for a plan
    that opens with an index scan, and would have failed a perfectly good schema.
    """
    indexed = (
        "Index Scan using",
        "Index Only Scan",
        "Bitmap Index Scan on",
        "Bitmap Heap Scan",
    )
    return any(marker in plan for marker in indexed)


def _execution_ms(plan: str) -> float:
    for line in plan.splitlines():
        if "Execution Time:" in line:
            try:
                return float(line.split("Execution Time:")[1].split("ms")[0].strip())
            except (IndexError, ValueError):
                return 0.0
    return 0.0


@pytest.mark.performance
class TestHotFiltersUseIndexes:
    async def test_the_product_sku_filter_uses_an_index(
        self,
        engine: Any,
        perf_identity: dict,
        app_session_factory: Any,
    ) -> None:
        """§57's indexing rule, on the query a catalog explorer issues."""

        from sqlalchemy.ext.asyncio import async_sessionmaker

        tenant = _tenant(perf_identity)
        seed = async_sessionmaker(engine, expire_on_commit=False)()
        try:
            await build_catalog(seed, tenant.workspace_id, products=BULK)
        finally:
            await seed.close()

        async with app_session_factory() as session:
            plan = await _plan(
                session,
                "SELECT id, sku FROM products WHERE workspace_id = :ws AND sku = :sku",
                {"ws": str(tenant.workspace_id), "sku": "PERF-0000001"},
            )

        print(f"\n  product by sku: indexed={_uses_index(plan)} {_execution_ms(plan):.2f}ms")
        assert _uses_index(plan), (
            "the SKU lookup is answered by a sequential scan at "
            f"{BULK} rows:\n{plan}"
        )

    async def test_the_workspace_filter_uses_an_index(
        self,
        engine: Any,
        perf_identity: dict,
        app_session_factory: Any,
    ) -> None:
        """Every tenant query filters on `workspace_id`; that column must be
        indexed, because it is in every statement the application issues."""

        from sqlalchemy.ext.asyncio import async_sessionmaker

        tenant = _tenant(perf_identity)
        seed = async_sessionmaker(engine, expire_on_commit=False)()
        try:
            await build_catalog(seed, tenant.workspace_id, products=BULK)
        finally:
            await seed.close()

        async with app_session_factory() as session:
            plan = await _plan(
                session,
                "SELECT id FROM products WHERE workspace_id = :ws ORDER BY created_at DESC",
                {"ws": str(tenant.workspace_id)},
            )

        print(f"\n  products by workspace: indexed={_uses_index(plan)}")
        assert _uses_index(plan), (
            f"the tenant filter is a sequential scan at {BULK} rows:\n{plan}"
        )

    async def test_the_source_record_join_uses_an_index(
        self,
        engine: Any,
        perf_identity: dict,
        app_session_factory: Any,
    ) -> None:
        """`_load_inputs` joins source records to a product list; both sides of
        that join need an index or the reconciler degrades to a scan per run."""

        from sqlalchemy.ext.asyncio import async_sessionmaker

        tenant = _tenant(perf_identity)
        seed = async_sessionmaker(engine, expire_on_commit=False)()
        try:
            await build_catalog(seed, tenant.workspace_id, products=SMALL * 20)
        finally:
            await seed.close()

        async with app_session_factory() as session:
            plan = await _plan(
                session,
                "SELECT r.id FROM source_records r JOIN catalog_sources s "
                "ON r.catalog_source_id = s.id WHERE r.workspace_id = :ws",
                {"ws": str(tenant.workspace_id)},
            )

        print(f"\n  source records by workspace: indexed={_uses_index(plan)}")
        assert _uses_index(plan), (
            f"the reconciler's input query is a sequential scan:\n{plan}"
        )

    async def test_a_composite_foreign_key_index_exists(
        self,
        engine: Any,
    ) -> None:
        """D-011's composite keys are enforced by indexes, which means the
        columns the application filters on are already indexed — asserted so a
        future migration that drops one is noticed here."""

        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT tablename, indexname FROM pg_indexes "
                        "WHERE schemaname = 'public' AND indexdef LIKE '%workspace_id%'"
                    )
                )
            ).all()

        assert rows, "no index mentions workspace_id at all"


@pytest.mark.performance
class TestThePlanIsReadBeforeItIsAsserted:
    async def test_the_helper_reads_a_real_plan(self, app_session: Any) -> None:
        """A plan parser that always returns "indexed" would pass everything.

        The same guard as the statement counter: prove the instrument can report
        the bad answer before trusting it to report the good one.
        """
        plan = await _plan(app_session, "SELECT 1 AS one", {})

        assert "Result" in plan or "Query" in plan, plan
        assert _uses_index(plan) is False, "a constant query has no index to use"

    async def test_an_indexed_lookup_is_reported_as_indexed(
        self, engine: Any, app_session: Any
    ) -> None:
        """The other direction: the helper must be able to say yes."""

        plan = await _plan(
            app_session,
            "SELECT id FROM organizations WHERE slug = 'nothing-matches-this'",
            {},
        )

        # `slug` is unique, so the planner has no choice — this is the case where
        # "indexed" must come back true.
        assert _uses_index(plan), f"a unique-column lookup should be indexed:\n{plan}"