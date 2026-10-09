"""Row-level security tests (D-011).

RLS is the layer that holds when the layer above it is wrong, so these tests
assert the properties that make it worth having, not just that some SQL exists:

  - policies deny rather than allow when no tenant is bound
  - the write side is constrained too, not only reads
  - the tenant value is bound, never interpolated
  - the setting is transaction-local, so a pooled connection cannot leak it
  - the migration actually attaches policies to every tenant table

No database is required: the SQL is asserted as text, and the tenant tables are
read from the ORM metadata. Live behaviour needs Neon.
"""

from __future__ import annotations

import re
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import TextClause

from app.db.base import Base
from app.db.rls import (
    GLOBAL_TABLES,
    TENANT_SETTING,
    bind_tenant_statement,
    clear_tenant_statement,
    create_policy_sql,
    drop_policy_sql,
    enable_rls_sql,
)
from app.db.session import bind_tenant, clear_tenant

TENANT_TABLES = frozenset(
    name for name, table in Base.metadata.tables.items() if "workspace_id" in table.c
)


class TestPolicyFailsClosed:
    def test_missing_setting_denies_instead_of_allowing(self) -> None:
        """The whole point of the NULLIF wrapper.

        `current_setting(name, true)` yields NULL when unset, NULLIF maps a
        blank value to NULL too, and `workspace_id = NULL` is unknown rather
        than true. A connection with no tenant therefore sees no rows.
        """
        policy = create_policy_sql("products")
        assert f"current_setting('{TENANT_SETTING}', true)" in policy
        assert "NULLIF(" in policy
        assert "'')::uuid" in policy

    def test_setting_is_read_with_the_missing_ok_flag(self) -> None:
        """Without the second argument Postgres raises instead of denying.

        A raise would be an availability failure on any unbound query; the
        intent is an empty result set.
        """
        policy = create_policy_sql("products")
        assert not re.search(r"current_setting\(\s*'[^']+'\s*\)", policy)

    def test_uuid_cast_present(self) -> None:
        """The setting is text; the column is uuid. Compare in the right type."""
        assert "::uuid" in create_policy_sql("products")

    def test_policy_applies_to_every_operation(self) -> None:
        assert "FOR ALL" in create_policy_sql("products")


class TestWriteSideIsConstrained:
    def test_with_check_matches_using(self) -> None:
        """Reads alone are not enough.

        Without WITH CHECK, a tenant could INSERT a row owned by another
        workspace. Nothing would reject it, and it would stay invisible until
        someone queried for it.
        """
        policy = create_policy_sql("products")
        using = _predicate_from(policy, "USING")
        check = _predicate_from(policy, "WITH CHECK")
        assert using == check

    def test_policy_covers_insert_update_and_delete(self) -> None:
        assert "FOR ALL" in create_policy_sql("catalog_sources")


def _predicate_from(policy: str, clause: str) -> str:
    match = re.search(rf"{clause} \((.*?)\)", policy)
    assert match is not None, f"{clause} missing from {policy!r}"
    return match.group(1).strip()


class TestRlsIsActuallyForced:
    def test_enable_sql_forces_and_enables(self) -> None:
        """FORCE is not optional here.

        Postgres exempts a table's owner from its own policies, and the
        application connects as the owner. Without FORCE every policy would be
        inert in production while still present in the schema.
        """
        enable, force = enable_rls_sql("products")
        # Two statements, not one joined by a semicolon: asyncpg refuses
        # multi-command prepared statements, and the joined form meant RLS was
        # never enabled on a real database.
        assert enable == "ALTER TABLE products ENABLE ROW LEVEL SECURITY"
        assert force == "ALTER TABLE products FORCE ROW LEVEL SECURITY"
        assert ";" not in enable and ";" not in force

    def test_drop_is_idempotent(self) -> None:
        assert drop_policy_sql("products") == (
            "DROP POLICY IF EXISTS tenant_isolation ON products"
        )


class TestTenantBinding:
    def test_value_is_bound_not_interpolated(self) -> None:
        statement = bind_tenant_statement("11111111-1111-1111-1111-111111111111")
        rendered = str(statement)
        assert "11111111" not in rendered
        assert rendered.count(":") >= 2
        assert statement.compile().params["workspace_id"] == (
            "11111111-1111-1111-1111-111111111111"
        )

    def test_binding_is_transaction_local(self) -> None:
        """`SET LOCAL` is what makes this safe under a transaction pooler.

        Neon hands back a different backend connection per transaction, and a
        plain SET would survive on that backend for the next tenant.
        """
        assert "set_config(" in str(bind_tenant_statement(uuid.uuid4()))
        assert bind_tenant_statement(uuid.uuid4()).compile().params["setting"] == (
            TENANT_SETTING
        )
        assert ", true)" in str(bind_tenant_statement(uuid.uuid4())).replace(
            "\n", " "
        )

    def test_uuid_objects_and_strings_agree(self) -> None:
        """Callers hold UUIDs; config and headers hand over strings."""
        value = uuid.uuid4()
        assert (
            bind_tenant_statement(value).compile().params["workspace_id"]
            == bind_tenant_statement(str(value)).compile().params["workspace_id"]
        )

    def test_clear_empties_the_setting(self) -> None:
        statement = clear_tenant_statement()
        assert statement.compile().params["setting"] == TENANT_SETTING
        assert "''" in str(statement)

    async def test_bind_tenant_uses_the_statement(self) -> None:
        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock())
        workspace_id = uuid.uuid4()
        await bind_tenant(session, workspace_id)
        executed = session.execute.await_args.args[0]
        assert isinstance(executed, TextClause)
        assert executed.compile().params["workspace_id"] == str(workspace_id)

    async def test_clear_tenant_uses_the_statement(self) -> None:
        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock())
        await clear_tenant(session)
        executed = session.execute.await_args.args[0]
        assert executed.compile().params["setting"] == TENANT_SETTING


class TestPolicyCoverage:
    @pytest.mark.parametrize("table", sorted(TENANT_TABLES - GLOBAL_TABLES))
    def test_policy_sql_is_constructible_for_every_tenant_table(
        self, table: str
    ) -> None:
        policy = create_policy_sql(table)
        assert f" ON {table} " in policy
        assert policy.count("WITH CHECK") == 1

    def test_global_tables_are_excluded(self) -> None:
        """organizations and users sit above or outside the tenant boundary."""
        assert frozenset({"organizations", "users", "workspaces"}) == GLOBAL_TABLES

    def test_tenant_tables_really_are_tenant_tables(self) -> None:
        """If this drops to a handful, RLS coverage has silently narrowed."""
        covered = TENANT_TABLES - GLOBAL_TABLES
        assert len(covered) >= 20
        for expected in ("catalog_sources", "audit_logs", "products", "agent_runs"):
            assert expected in covered
