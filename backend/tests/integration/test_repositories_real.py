"""Repositories against real SQL (Phase 24, §40).

The repository layer's entire contract is that its queries are valid SQL and that
they are tenant-scoped. A fake session can answer "does the service ask the
repository the right thing" and nothing else — it cannot tell whether the query
compiles, whether the ordering clause is legal, whether `IN ()` is a syntax
error, or whether the tenant predicate agrees with the policy on the table.

Every block runs as a **non-superuser** with the tenant bound, which is how the
application connects. A superuser bypasses RLS entirely, so isolation assertions
run against `postgres` would pass while proving nothing — and an unbound
transaction must return nothing, which only a real policy does.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.db.base import TenantContext
from app.models.catalog import Product
from app.services.catalog.repositories import ProductRepository
from tests.integration.conftest import make_catalog, tenant_session

pytestmark = pytest.mark.integration


def _product(workspace_id: Any, catalog_id: Any, sku: str) -> Product:
    return Product(
        id=uuid4(),
        workspace_id=workspace_id,
        catalog_id=catalog_id,
        sku=sku,
        status="active",
        version=1,
        attributes={"material": "ceramic"},
    )


class TestTenantRepositoryAgainstSql:
    async def test_add_get_and_exists_round_trip(
        self, app_session_factory: Any, workspace: TenantContext
    ) -> None:
        async with tenant_session(app_session_factory, workspace) as session:
            catalog = await make_catalog(session, workspace)
            repo = ProductRepository(session, workspace)
            saved = await repo.add(_product(workspace.workspace_id, catalog.id, "RT-1"))
            await session.flush()

            assert await repo.exists(saved.id)
            fetched = await repo.get(saved.id)
            assert fetched is not None
            assert fetched.sku == "RT-1"

    async def test_a_foreign_workspace_row_is_not_found_by_id(
        self, app_session_factory: Any, workspace: TenantContext, other_workspace: TenantContext
    ) -> None:
        """The repository's `workspace_id` predicate, not luck.

        `get()` filters by the repository's tenant, so a row belonging to
        another workspace returns `None` rather than that workspace's data —
        which is what makes a mistaken id a miss instead of a leak.
        """
        async with tenant_session(app_session_factory, other_workspace) as their_side:
            their_catalog = await make_catalog(their_side, other_workspace)
            their_row = await ProductRepository(their_side, other_workspace).add(
                _product(other_workspace.workspace_id, their_catalog.id, "THEIRS-1")
            )
            await their_side.commit()

        async with tenant_session(app_session_factory, workspace) as mine:
            await make_catalog(mine, workspace)
            repo = ProductRepository(mine, workspace)

            assert await repo.get(their_row.id) is None
            assert not await repo.exists(their_row.id)
            assert [row.sku for row in await repo.list()] == []

    async def test_list_orders_and_paginates(
        self, app_session_factory: Any, workspace: TenantContext
    ) -> None:
        async with tenant_session(app_session_factory, workspace) as session:
            catalog = await make_catalog(session, workspace)
            repo = ProductRepository(session, workspace)
            for index in range(5):
                await repo.add(_product(workspace.workspace_id, catalog.id, f"SORT-{4 - index}"))
            await session.flush()

            first_page = [
                row.sku
                for row in await repo.list(limit=2, offset=0, order_by=Product.sku)
            ]
            second_page = [
                row.sku
                for row in await repo.list(limit=2, offset=2, order_by=Product.sku)
            ]
            counted = await repo.count()

        assert first_page == ["SORT-0", "SORT-1"]
        assert second_page == ["SORT-2", "SORT-3"]
        assert counted == 5

    async def test_where_and_in_filters_compile_and_filter(
        self, app_session_factory: Any, workspace: TenantContext
    ) -> None:
        async with tenant_session(app_session_factory, workspace) as session:
            catalog = await make_catalog(session, workspace)
            repo = ProductRepository(session, workspace)
            for sku in ("FILTER-A", "FILTER-B", "FILTER-C"):
                await repo.add(_product(workspace.workspace_id, catalog.id, sku))
            await session.flush()

            by_sku = [row.sku for row in await repo.list(where=Product.sku == "FILTER-B")]
            by_set = {
                row.sku for row in await repo.list(where=Product.sku.in_(["FILTER-A", "FILTER-C"]))
            }
            counted = await repo.count(where=Product.sku.like("FILTER-%"))

        assert by_sku == ["FILTER-B"]
        assert by_set == {"FILTER-A", "FILTER-C"}
        assert counted == 3

    async def test_soft_deleted_rows_are_excluded_from_reads(
        self, app_session_factory: Any, workspace: TenantContext
    ) -> None:
        async with tenant_session(app_session_factory, workspace) as session:
            catalog = await make_catalog(session, workspace)
            repo = ProductRepository(session, workspace)
            kept = await repo.add(_product(workspace.workspace_id, catalog.id, "SOFT-KEEP"))
            gone = await repo.add(_product(workspace.workspace_id, catalog.id, "SOFT-GONE"))
            await session.flush()

            deleted = await repo.delete(gone.id)
            await session.flush()

            skus = [row.sku for row in await repo.list()]
            assert await repo.get(kept.id) is not None
            assert await repo.count(where=Product.id == gone.id) == 0

        assert deleted
        assert skus == ["SOFT-KEEP"]

    async def test_a_repository_refuses_to_be_built_without_a_tenant(self) -> None:
        """The guard that makes every other test's premise true."""

        with pytest.raises(ValueError, match="TenantContext"):
            ProductRepository(None, "not-a-tenant")  # type: ignore[arg-type]

    async def test_count_on_an_empty_table_is_zero_not_an_error(
        self, app_session_factory: Any, workspace: TenantContext
    ) -> None:
        async with tenant_session(app_session_factory, workspace) as session:
            await make_catalog(session, workspace)

            assert await ProductRepository(session, workspace).count() == 0

    async def test_an_unbound_transaction_sees_nothing(
        self, app_session_factory: Any, workspace: TenantContext
    ) -> None:
        """The fail-closed property D-011 depends on, observed."""
        from sqlalchemy import select

        async with tenant_session(app_session_factory, workspace) as session:
            catalog = await make_catalog(session, workspace)
            await ProductRepository(session, workspace).add(
                _product(workspace.workspace_id, catalog.id, "CLOSED-1")
            )
            await session.commit()  # deliberate: the reader below is unbound

        async with app_session_factory() as unbound:
            visible = (await unbound.execute(select(Product.sku))).scalars().all()

        assert list(visible) == []