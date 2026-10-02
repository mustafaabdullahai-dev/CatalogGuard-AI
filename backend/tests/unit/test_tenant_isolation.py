"""Tenant isolation tests (D-011).

The premise of this file: with a shared schema, a missing tenant predicate
is a data breach. These tests assert that isolation does not depend on any
individual query being written correctly.

Layering under test:
  1. `TenantRepository` refuses to exist without a tenant.
  2. Scoped queries filter by workspace_id.
  3. Soft-delete filtering is applied centrally, not per call site.
  4. The schema blocks cross-tenant foreign keys at the database level.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import UniqueConstraint, select

from app.db.base import TENANT_KEY_COLUMNS, Base, TenantContext, tenant_filter
from app.db.repository import TenantRepository
from app.models import Product


def _tenant() -> TenantContext:
    return TenantContext(workspace_id=uuid.uuid4(), user_id=uuid.uuid4())


def _other_tenant() -> TenantContext:
    return TenantContext(workspace_id=uuid.uuid4(), user_id=uuid.uuid4())


def _session() -> MagicMock:
    """An AsyncSession stand-in.

    `add` is synchronous, so the mock must be sync — a blanket AsyncMock
    returns an un-awaited coroutine and hides real call assertions.
    """
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    session.flush = AsyncMock(return_value=None)
    session.delete = MagicMock()
    session.add = MagicMock()
    return session


class ProductRepo(TenantRepository[Product]):
    model = Product


class TestRepositoryRequiresTenant:
    def test_cannot_construct_without_tenant_context(self) -> None:
        """The central safeguard. No tenant means no repository."""
        with pytest.raises(ValueError, match="TenantContext"):
            ProductRepo(_session(), None)  # type: ignore[arg-type]

    def test_rejects_wrong_tenant_type(self) -> None:
        with pytest.raises(ValueError, match="TenantContext"):
            ProductRepo(_session(), uuid.uuid4())  # type: ignore[arg-type]

    def test_construction_succeeds_with_tenant(self) -> None:
        repo = ProductRepo(_session(), _tenant())
        assert repo.workspace_id is not None


class TestTenantScopeIsApplied:
    def test_scoped_statement_filters_workspace_and_soft_delete(self) -> None:
        repo = ProductRepo(_session(), _tenant())
        stmt = repo._scoped(select(Product))
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "workspace_id" in sql
        assert "deleted_at" in sql

    def test_tenant_filter_helper(self) -> None:
        ctx = _tenant()
        predicate = tenant_filter(Product.workspace_id, ctx)
        assert predicate.right.value == ctx.workspace_id

    def test_repository_pins_one_tenant(self) -> None:
        repo = ProductRepo(_session(), _tenant())
        assert repo.workspace_id == repo._tenant.workspace_id

    def test_distinct_tenants_do_not_compare_equal(self) -> None:
        assert _tenant() != _other_tenant()


class TestInsertGuards:
    async def test_stamps_workspace_when_absent(self) -> None:
        """An unstamped row is stamped with the request tenant, not trusted."""
        repo = ProductRepo(_session(), _tenant())
        product = Product(sku="SHIRT-001", catalog_id=uuid.uuid4())
        product.workspace_id = None

        await repo.add(product)

        assert product.workspace_id == repo.workspace_id
        repo._session.add.assert_called_once_with(product)

    async def test_refuses_cross_tenant_insert(self) -> None:
        """A row stamped for another tenant must be rejected, not persisted."""
        repo = ProductRepo(_session(), _tenant())
        product = Product(sku="SHIRT-001", catalog_id=uuid.uuid4())
        product.workspace_id = _other_tenant().workspace_id

        with pytest.raises(ValueError, match="does not match the request tenant"):
            await repo.add(product)

        repo._session.add.assert_not_called()


class TestTenantScopeCannotBeReassigned:
    async def test_update_fields_drops_workspace_id(self) -> None:
        """A caller must never move a row to another tenant via a patch."""
        repo = ProductRepo(_session(), _tenant())
        product = Product(sku="SHIRT-001", catalog_id=uuid.uuid4(), workspace_id=repo.workspace_id)
        repo.get = AsyncMock(return_value=product)  # type: ignore[method-assign]

        updated = await repo.update_fields(
            product.id, title="Renamed", workspace_id=_other_tenant().workspace_id
        )

        assert updated.workspace_id == repo.workspace_id
        assert product.title == "Renamed"

    async def test_bulk_update_drops_workspace_id_and_id(self) -> None:
        repo = ProductRepo(_session(), _tenant())
        repo._session.execute = AsyncMock(return_value=MagicMock(rowcount=2))

        rows = await repo.bulk_update(
            [uuid.uuid4(), uuid.uuid4()],
            title="Renamed",
            workspace_id=_other_tenant().workspace_id,
            id=uuid.uuid4(),
        )

        assert rows == 2
        stmt = repo._session.execute.await_args[0][0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "Renamed" in sql
        # workspace_id appears only in the WHERE clause, never in SET.
        assert sql.count("workspace_id") == 1
        assert "id = " not in sql.split("WHERE")[0]

    async def test_hard_delete_is_tenant_scoped(self) -> None:
        """Even permanent deletion must not cross tenants."""
        repo = ProductRepo(_session(), _tenant())
        repo._session.execute = AsyncMock(return_value=MagicMock(rowcount=1))

        assert await repo.hard_delete(uuid.uuid4()) is True

        sql = str(repo._session.execute.await_args[0][0])
        assert "workspace_id" in sql
        assert "DELETE" in sql

    async def test_soft_delete_sets_timestamp_not_row_removal(self) -> None:
        """D-012: user-facing delete must not destroy history."""
        repo = ProductRepo(_session(), _tenant())
        product = Product(sku="SHIRT-001", catalog_id=uuid.uuid4(), workspace_id=repo.workspace_id)
        repo.get = AsyncMock(return_value=product)  # type: ignore[method-assign]

        assert await repo.delete(product.id) is True
        assert product.deleted_at is not None
        repo._session.delete.assert_not_called()


class TestSchemaLevelIsolation:
    """The database is the backstop, not the ORM."""

    def test_products_fk_pairs_workspace_with_catalog(self) -> None:
        table = Base.metadata.tables["products"]
        composite = [
            fk
            for fk in table.foreign_key_constraints
            if {c.name for c in fk.columns} >= {"workspace_id", "catalog_id"}
        ]
        assert composite, "products must have a composite workspace/catalog FK"

    def test_relationships_pair_workspace_with_both_ends(self) -> None:
        """A relationship cannot span tenants on either side."""
        table = Base.metadata.tables["product_relationships"]
        pairs = {
            frozenset(c.name for c in fk.columns)
            for fk in table.foreign_key_constraints
        }
        assert frozenset({"workspace_id", "parent_id"}) in pairs
        assert frozenset({"workspace_id", "child_id"}) in pairs

    def test_sku_is_unique_per_workspace_not_globally(self) -> None:
        """Two tenants may legitimately use the same SKU."""
        table = Base.metadata.tables["products"]
        uniques = {
            tuple(sorted(c.name for c in uq.columns))
            for uq in table.constraints
            if uq.__class__.__name__ == "UniqueConstraint"
        }
        assert ("sku", "workspace_id") in uniques
        assert ("sku",) not in uniques

    def test_marketplace_listing_unique_per_workspace(self) -> None:
        table = Base.metadata.tables["marketplace_products"]
        uniques = {
            tuple(sorted(c.name for c in uq.columns))
            for uq in table.constraints
            if uq.__class__.__name__ == "UniqueConstraint"
        }
        assert ("external_product_id", "marketplace", "workspace_id") in uniques

    def test_audit_log_has_no_updated_or_deleted_column(self) -> None:
        """D-013: append-only is structural, not a convention."""
        columns = Base.metadata.tables["audit_logs"].columns
        assert "updated_at" not in columns
        assert "deleted_at" not in columns

    def test_every_tenant_table_has_workspace_id(self) -> None:
        # Identity and session tables sit at or outside the boundary: a user
        # belongs to several workspaces and a session outlives any one of them.
        exempt = {
            "organizations",
            "users",
            "workspaces",
            "auth_sessions",
            "login_throttles",
            # D-017. Both are account-recovery tables, and account recovery
            # happens precisely when the person cannot say which workspace they
            # belong to. Tenant-scoping either one would make it unusable in the
            # case it exists for. Neither stores catalog data, so neither can
            # leak another tenant's.
            "password_reset_tokens",
            "email_outbox",
        }
        missing = [
            name
            for name, table in Base.metadata.tables.items()
            if name not in exempt and "workspace_id" not in table.columns
        ]
        assert not missing, f"tables missing workspace_id: {missing}"


class TestAuditImmutabilityTriggers:
    def test_audit_table_declares_no_update_path(self) -> None:
        """Guard against someone adding a mutable field later."""
        columns = set(Base.metadata.tables["audit_logs"].columns.keys())
        forbidden = {"updated_at", "deleted_at", "revision"}
        assert not (columns & forbidden)


class TestRoleBoundariesInSchema:
    def test_connection_table_has_no_plaintext_token_column(self) -> None:
        """Section 33: credentials are never plaintext."""
        columns = set(Base.metadata.tables["marketplace_connections"].columns.keys())
        assert "encrypted_credentials" in columns
        forbidden = {"access_token", "refresh_token", "client_secret", "api_key", "password"}
        assert not (columns & forbidden)

    def test_proposal_confidence_is_bounded_in_schema(self) -> None:
        """Section 37: 0.99 must not be able to mean 'execute'."""
        table = Base.metadata.tables["change_proposals"]
        checks = [c for c in table.constraints if c.__class__.__name__ == "CheckConstraint"]
        assert any("confidence" in str(c.sqltext) for c in checks)

    def test_execution_attempts_are_bounded_in_schema(self) -> None:
        """Section 21: retries must not be unbounded."""
        table = Base.metadata.tables["execution_jobs"]
        checks = [c for c in table.constraints if c.__class__.__name__ == "CheckConstraint"]
        assert any("max_attempts" in str(c.sqltext) for c in checks)

    def test_relationship_cannot_be_self_referential(self) -> None:
        table = Base.metadata.tables["product_relationships"]
        checks = [c for c in table.constraints if c.__class__.__name__ == "CheckConstraint"]
        assert any("parent_id" in str(c.sqltext) for c in checks)


class TestTenantKeyConstraintExists:
    """Postgres will reject a composite FK whose parent pair is not unique."""

    @staticmethod
    def _tenant_tables() -> list:
        return [t for t in Base.metadata.tables.values() if "workspace_id" in t.columns]

    def test_every_tenant_table_declares_the_tenant_key(self) -> None:
        missing = [
            table.name
            for table in self._tenant_tables()
            if frozenset(TENANT_KEY_COLUMNS)
            not in {
                frozenset(c.name for c in uq.columns)
                for uq in table.constraints
                if isinstance(uq, UniqueConstraint)
            }
        ]
        assert not missing, f"tables missing UNIQUE(workspace_id, id): {missing}"

    def test_composite_foreign_keys_reference_a_valid_tenant_key(self) -> None:
        """Every composite FK must resolve against a real unique pair."""
        invalid: list[str] = []
        for table in Base.metadata.tables.values():
            for fk in table.foreign_key_constraints:
                if len(fk.columns) < 2:
                    continue
                parent = next(iter(fk.elements)).column.table
                parent_cols = tuple(sorted(el.column.name for el in fk.elements))
                resolvable = parent_cols == tuple(
                    sorted(parent.primary_key.columns.keys())
                ) or any(
                    isinstance(uq, UniqueConstraint)
                    and tuple(sorted(c.name for c in uq.columns)) == parent_cols
                    for uq in parent.constraints
                )
                if not resolvable:
                    invalid.append(f"{table.name} -> {parent.name}{list(parent_cols)}")
        assert not invalid, f"composite FKs with no unique parent key: {invalid}"

    def test_constraint_names_are_deterministic(self) -> None:
        """Unnamed constraints make down-migrations fragile."""
        for table in self._tenant_tables():
            for constraint in table.constraints:
                assert constraint.name, f"unnamed {type(constraint).__name__} on {table.name}"


def test_metadata_is_internally_consistent() -> None:
    """Guards against an accidentally broken metadata configuration."""
    # 30 through D-016, plus D-017's password_reset_tokens, workspace_invitations
    # and email_outbox.
    assert len(Base.metadata.tables) == 33
    names = [t.name for t in Base.metadata.tables.values()]
    assert len(names) == len(set(names))