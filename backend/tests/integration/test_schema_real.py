"""What the database actually contains (Phase 24, §40).

`test_migration_parity.py` asserts the migration and the ORM agree with each
other. That is a statement about two pieces of text. This file asks the database:
are the tables there, is the HNSW index built with the operator class its query
needs, is RLS actually enabled and forced, does a CHECK reject a bad row, and
does the append-only trigger refuse an update.

Two of those five were broken before this phase and no text comparison could
have found them.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base, TenantContext
from app.models.reconciliation import AuditLog
from tests.integration.conftest import make_catalog

pytestmark = pytest.mark.integration


async def _rows(session: AsyncSession, sql: str) -> list[tuple[object, ...]]:
    result = await session.execute(text(sql))
    return [tuple(row) for row in result.all()]


class TestTheSchemaExists:
    async def test_every_table_in_the_orm_exists_in_the_database(
        self, db_session: AsyncSession
    ) -> None:
        existing = {
            name
            for (name,) in await _rows(
                db_session,
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'",
            )
        }

        missing = set(Base.metadata.tables) - existing
        assert not missing, f"tables in the ORM but not in the database: {sorted(missing)}"

    async def test_the_migration_is_recorded_as_applied(self, db_session: AsyncSession) -> None:
        rows = await _rows(db_session, "SELECT version_num FROM alembic_version")

        assert rows == [("0001",)]

    async def test_pgvector_is_installed(self, db_session: AsyncSession) -> None:
        rows = await _rows(
            db_session, "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        )

        assert rows, "the migration creates the extension; retrieval needs it"


class TestRetrievalIndexes:
    async def test_the_hnsw_index_carries_the_operator_class_its_query_uses(
        self, db_session: AsyncSession
    ) -> None:
        """pgvector 0.8 has no default opclass for `hnsw`.

        The migration shipped without one, so `alembic upgrade` failed on the
        first real database and hybrid retrieval had never been deployed. Cosine
        is what `ComplianceChunk.embedding.cosine_distance` queries with, so the
        index must say so.
        """
        rows = await _rows(
            db_session,
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_chunks_embedding_hnsw'",
        )

        assert rows, "the HNSW index is missing"
        definition = str(rows[0][0])
        assert "vector_cosine_ops" in definition
        assert "ef_construction" in definition

    async def test_the_gin_index_over_the_search_vector_exists(
        self, db_session: AsyncSession
    ) -> None:
        rows = await _rows(
            db_session,
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_chunks_search_vector'",
        )

        assert rows and "USING gin" in str(rows[0][0])


class TestRowLevelSecurity:
    async def test_rls_is_enabled_and_forced_on_every_tenant_table(
        self, db_session: AsyncSession
    ) -> None:
        """Enabled is not enough: a table's owner bypasses its own policies.

        The application connects as the owner, so without `FORCE` the policies
        D-011 depends on would be inert in production while every offline test
        still passed.
        """
        rows = await _rows(
            db_session,
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind = 'r' AND n.nspname = 'public'",
        )
        by_name = {name: (enabled, forced) for name, enabled, forced in rows}
        tenant_tables = {
            name for name, table in Base.metadata.tables.items() if "workspace_id" in table.c
        }

        def missing(index: int) -> list[str]:
            return [name for name in sorted(tenant_tables) if not by_name.get(name, (0, 0))[index]]

        not_enabled = missing(0)
        not_forced = missing(1)

        assert not not_enabled, f"RLS not enabled on: {not_enabled}"
        assert not not_forced, f"RLS not forced on: {not_forced}"

    async def test_every_tenant_table_has_the_isolation_policy(
        self, db_session: AsyncSession
    ) -> None:
        rows = await _rows(
            db_session, "SELECT tablename FROM pg_policies WHERE policyname = 'tenant_isolation'"
        )
        policies = {name for (name,) in rows}
        tenant_tables = {
            name for name, table in Base.metadata.tables.items() if "workspace_id" in table.c
        }

        assert not tenant_tables - policies

    async def test_the_policy_denies_when_no_workspace_is_bound(
        self,
        app_session: AsyncSession,
        app_session_factory: Any,
    ) -> None:
        """Both sides of the policy: reads and writes.

        A row written by a bound transaction must be invisible to an unbound one,
        and a row an unbound transaction tries to write must be refused. The
        write side is the half that matters most: without `WITH CHECK`, a tenant
        could attribute a row to another workspace and the read policy would
        only expose it later.
        """
        from sqlalchemy import select
        from sqlalchemy.exc import DBAPIError

        from app.models.catalog import Product

        workspace = uuid4()
        catalog = await make_catalog(app_session, TenantContext(workspace_id=workspace))
        app_session.add(
            Product(
                id=uuid4(),
                workspace_id=workspace,
                catalog_id=catalog.id,
                sku="RLS-1",
                status="active",
                version=1,
                attributes={},
            )
        )
        await app_session.commit()

        # A separate transaction as the same role, with no tenant bound.
        async with app_session_factory() as reader:
            visible = (await reader.execute(select(Product.id))).scalars().all()
        assert list(visible) == [], "an unbound transaction must see nothing"

        async with app_session_factory() as writer:
            with pytest.raises(DBAPIError):
                writer.add(
                    Product(
                        id=uuid4(),
                        workspace_id=workspace,
                        catalog_id=catalog.id,
                        sku="RLS-2",
                        status="active",
                        version=1,
                        attributes={},
                    )
                )
                await writer.commit()

    async def test_one_workspace_cannot_read_another_workspaces_rows(
        self,
        app_session_factory: Any,
    ) -> None:
        """The isolation D-011 promises, observed rather than asserted.

        Two rows, two workspaces, one transaction each. The second tenant's
        unscoped read returns exactly its own row — no `where` clause anywhere,
        which is the point: RLS is what makes an unscoped read safe.
        """
        from sqlalchemy import select

        from app.db.session import bind_tenant
        from app.models.catalog import Product

        first, second = uuid4(), uuid4()
        for workspace, sku in ((first, "ISO-A"), (second, "ISO-B")):
            async with app_session_factory() as session:
                context = TenantContext(workspace_id=workspace)
                catalog = await make_catalog(session, context, name=f"Catalog {sku}")
                session.add(
                    Product(
                        id=uuid4(),
                        workspace_id=workspace,
                        catalog_id=catalog.id,
                        sku=sku,
                        status="active",
                        version=1,
                        attributes={},
                    )
                )
                # Bound again because the flush above is inside this
                # transaction: `SET LOCAL` survives until commit, and this is
                # the same transaction.
                await bind_tenant(session, workspace)
                await session.commit()

        async with app_session_factory() as session:
            await bind_tenant(session, second)
            skus = list(
                (await session.execute(select(Product.sku))).scalars().all()
            )

        assert skus == ["ISO-B"]


class TestConstraintsAndTriggers:
    async def test_a_check_constraint_rejects_an_impossible_execution_job(
        self, db_session: AsyncSession
    ) -> None:
        """`attempt_count <= max_attempts` is the engine's bound, enforced here.

        Phase 20 relies on the database to make the loop unbudgeable; only a
        real database can say whether it does.
        """
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await db_session.execute(
                text(
                    "INSERT INTO execution_jobs (id, workspace_id, change_proposal_id, "
                    "connector, operation, status, attempt_count, max_attempts, "
                    "created_at, updated_at) "
                    "VALUES (:id, :workspace, :proposal, 'mock', 'update_product', "
                    "'failed', 5, 3, now(), now())"
                ),
                {"id": uuid4(), "workspace": uuid4(), "proposal": uuid4()},
            )

    async def test_the_audit_log_refuses_an_update(self, db_session: AsyncSession) -> None:
        """D-013: append-only, enforced by a trigger rather than by convention."""
        from sqlalchemy.exc import DBAPIError

        entry = AuditLog(
            workspace_id=uuid4(),
            action="test.append_only",
            entity_type="test",
            payload_hash="hash",
        )
        db_session.add(entry)
        await db_session.commit()

        with pytest.raises(DBAPIError):
            await db_session.execute(
                text("UPDATE audit_logs SET action = 'tampered' WHERE id = :id"),
                {"id": entry.id},
            )