"""Row-level security (D-011).

The ORM already scopes every query to one workspace. RLS exists for the paths
that do not go through the ORM:

  - raw SQL in a migration or an admin script
  - a reporting query written by hand
  - a bug that constructs a statement without the tenant predicate
  - a service account holding its own credentials

`TenantRepository` is the first layer. This is the second, and the two are
independent: dropping the repository would leave RLS, and disabling RLS would
leave the repository.

How it works
------------
Policies compare `workspace_id` against a session variable that the
application sets per request:

    SELECT set_config('app.current_workspace', '<uuid>', true)

The third argument makes it `SET LOCAL`, scoping the value to the enclosing
transaction. That is the detail that makes this safe with a transaction-mode
pooler like Neon: a returned connection cannot carry one tenant's context into
the next request.

The policy denies when the setting is missing or empty, so a connection with no
bound tenant sees nothing rather than everything.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.sql.elements import TextClause

# Read by every policy. Named to be unmistakable in `pg_stat_activity` and in a
# query log.
TENANT_SETTING = "app.current_workspace"

# Tables that intentionally carry no tenant column and therefore no policy.
#
# `organizations` sits above the tenant boundary. `users` is global because one
# person may belong to several workspaces and their identity must be consistent
# across them. `workspaces` is the boundary itself, and a policy keyed on its
# own `workspace_id` would be circular.
GLOBAL_TABLES = frozenset(
    {
        "organizations",
        "users",
        "workspaces",
    }
)


def _tenant_predicate() -> str:
    """SQL comparing a row's `workspace_id` to the bound workspace.

    `current_setting(..., true)` returns NULL instead of raising when the
    variable is unset, which is what lets an unbound tenant be expressed as a
    comparison that is simply false.
    """
    return (
        f"workspace_id = NULLIF(current_setting('{TENANT_SETTING}', true), '')::uuid"
    )


def bind_tenant_statement(workspace_id: UUID | str) -> TextClause:
    """Bind the current transaction to one workspace.

    Values are always bound, never interpolated, so a hostile value cannot
    reach the parser. Call once per transaction, before any tenant query:

        await session.execute(bind_tenant_statement(tenant.workspace_id))

    Requires an open transaction: with `is_local` semantics the setting is
    discarded at commit or rollback, which is the intended containment.
    """
    return text("SELECT set_config(:setting, :workspace_id, true)").bindparams(
        setting=TENANT_SETTING,
        workspace_id=str(workspace_id),
    )


def clear_tenant_statement() -> TextClause:
    """Clear the tenant context inside the current transaction.

    Needed after privileged work in the same transaction. Without it, a
    workspace set for one operation would silently scope the next one.
    """
    return text("SELECT set_config(:setting, '', true)").bindparams(
        setting=TENANT_SETTING
    )


def create_policy_sql(table_name: str) -> str:
    """`CREATE POLICY` statement for one tenant-owned table.

    `WITH CHECK` closes the write side. Without it a tenant could insert a row
    attributed to another workspace, which no read-side policy would catch
    until someone queried for it.
    """
    predicate = _tenant_predicate()
    return (
        f"CREATE POLICY tenant_isolation ON {table_name} "
        f"FOR ALL USING ({predicate}) WITH CHECK ({predicate})"
    )


def enable_rls_sql(table_name: str) -> str:
    """Enable and *force* RLS for one table.

    `FORCE` is not redundant. Postgres exempts a table's owner from its own
    policies, and the application connects as the owner of these tables, so
    without `FORCE` every policy would be inert in practice.
    """
    return (
        f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY; "
        f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;"
    )


def drop_policy_sql(table_name: str) -> str:
    return f"DROP POLICY IF EXISTS tenant_isolation ON {table_name}"
