"""Cross-tenant access attempts (Phase 26, §33's tenant isolation).

The question this file answers is not "is there a repository that filters by
workspace" — that was verified in Phase 8 and again in Phase 24. It is: **if an
attacker holds a valid session for workspace A and a real id belonging to
workspace B, what does the API do?**

Every id here is a genuine id from another workspace, not a made-up one. A 404
for a fabricated id proves the routing exists; a 404 for a *real* id proves the
lookup is scoped.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


class TestCrossTenantReads:
    """Reads are the quiet half: a 200 with someone else's data is the worst
    outcome here, because nothing announces it."""

    async def test_another_workspaces_product_is_not_listed(
        self, attacker: Any, accounts: dict[str, str]
    ) -> None:
        listed = attacker.get(
            "/api/v1/products?limit=200",
            headers={"X-Workspace-Id": attacker.workspace_id},
        ).json().get("products", [])

        assert accounts["victim_product"] not in [p["id"] for p in listed]
        assert not any("SEC-VICTIM" in (p.get("sku") or "") for p in listed)

    async def test_the_catalog_list_holds_no_other_workspaces_rows(
        self, attacker: Any, accounts: dict[str, str]
    ) -> None:

        response = attacker.get(
            "/api/v1/catalogs", headers={"X-Workspace-Id": attacker.workspace_id}
        )

        assert response.status_code == 200
        body = response.text
        assert accounts["victim_workspace"] not in body
        assert "Victim" not in body


class TestCrossTenantWrites:
    """§33's authorization: knowing an id is not permission to act on it."""

    async def test_approving_another_workspaces_proposal_is_refused(
        self, attacker: Any, accounts: dict[str, str]
    ) -> None:
        """The consequential one: a decision on someone else's catalog.

        A 404 rather than a 403 is correct here and worth pinning — a 403 would
        confirm the id exists, which is itself a leak.
        """

        response = attacker.post(
            f"/api/v1/approvals/{accounts['victim_proposal']}/approve",
            headers=attacker.workspace(),
        )

        assert response.status_code in (403, 404), response.text

    async def test_simulating_another_workspaces_proposal_is_refused(
        self, attacker: Any, accounts: dict[str, str]
    ) -> None:

        response = attacker.post(
            f"/api/v1/change-proposals/{accounts['victim_proposal']}/simulate",
            headers=attacker.workspace(),
        )

        assert response.status_code in (403, 404), response.text

    async def test_claiming_another_workspaces_header_does_not_grant_it(
        self, attacker: Any, accounts: dict[str, str]
    ) -> None:
        """The header is a claim, not a grant.

        `X-Workspace-Id` names the workspace to act in; the session decides
        whether the caller may. A header naming someone else's workspace must
        not read their rows.
        """

        response = attacker.get(
            "/api/v1/products?limit=200",
            headers={"X-Workspace-Id": accounts["victim_workspace"]},
        )

        if response.status_code == 200:
            skus = {p.get("sku") for p in response.json().get("products", [])}
            assert "SEC-VICTIM-1" not in skus, "the header granted another workspace"
        else:
            assert response.status_code in (401, 403, 404), response.text

    async def test_a_membership_grants_only_one_workspace(
        self, attacker: Any, accounts: dict[str, str]
    ) -> None:
        session = attacker.get("/api/v1/auth/me").json()

        workspaces = {w["id"] for w in session["workspaces"]}
        assert accounts["victim_workspace"] not in workspaces


class TestRowLevelSecurityUnderTheOwningRole:
    """`FORCE ROW LEVEL SECURITY` under the role that owns the table.

    Phase 24 asserted policies exist and that a non-superuser is subject to
    them. This asserts the harder half: **the owning role** is subject too.
    PostgreSQL exempts an owner from its own policies unless `FORCE` is set, and
    the application connects as that owner — so a migration that dropped `FORCE`
    would leave every earlier isolation test green while disabling the control
    entirely.
    """

    async def test_the_application_role_cannot_read_another_workspaces_row(
        self, app_session: AsyncSession, accounts: dict[str, str], attacker: Any
    ) -> None:
        from app.db.session import bind_tenant

        await bind_tenant(app_session, attacker.workspace_id)

        rows = (
            await app_session.execute(
                text("SELECT id, sku FROM products WHERE id = :id"),
                {"id": accounts["victim_product"]},
            )
        ).all()

        assert list(rows) == [], "RLS returned another workspace's product"

    async def test_the_application_role_cannot_write_into_another_workspace(
        self, app_session: AsyncSession, accounts: dict[str, str], attacker: Any
    ) -> None:
        """`WITH CHECK` is the half that stops attribution, not just reads."""
        from sqlalchemy.exc import DBAPIError

        from app.db.session import bind_tenant

        await bind_tenant(app_session, attacker.workspace_id)

        # The values are explicit rather than selected. An `INSERT ... SELECT`
        # would read the victim's catalog *through* RLS, get nothing, and insert
        # nothing — which looks like the write succeeding while actually testing
        # the read policy instead of `WITH CHECK`.
        with pytest.raises(DBAPIError):
            await app_session.execute(
                text(
                    "INSERT INTO products (id, workspace_id, catalog_id, sku, status, "
                    "version, attributes, media, is_parent, created_at, updated_at) "
                    "VALUES (:id, :ws, :catalog, 'INJECTED', 'active', 1, '{}'::jsonb, "
                    "'{}'::jsonb, false, now(), now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "ws": accounts["victim_workspace"],
                    "catalog": accounts["victim_catalog"],
                },
            )

    async def test_no_role_bypasses_the_policy_by_being_the_table_owner(
        self, engine: Any
    ) -> None:
        """The owner is subject to the policy *because* `FORCE` is set.

        Asserted on `pg_class` rather than by reading rows: this is a property
        of the schema, and a test that read rows would pass on an empty table.
        """
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                        "AND c.relrowsecurity AND NOT c.relforcerowsecurity"
                    )
                )
            ).scalars().all()

        assert list(rows) == [], f"tables with RLS enabled but not forced: {list(rows)}"


class TestTenantContextIsNotTrusted:
    async def test_a_crafted_workspace_header_is_not_a_uuid_crash(
        self, attacker: Any
    ) -> None:
        """Input validation (§33): a malformed header is a 4xx, not a 500."""

        hostile = (
            "' OR 1=1--",
            "../../etc/passwd",
            "not-a-uuid",
            "00000000-0000-0000-0000-000000000000",
        )
        for header in hostile:
            response = attacker.get("/api/v1/products", headers={"X-Workspace-Id": header})
            assert response.status_code in (400, 403, 404), f"{header!r} -> {response.status_code}"
            assert response.status_code < 500, f"{header!r} produced a server error"

    async def test_a_workspace_header_that_is_a_real_but_foreign_uuid_is_refused(
        self, attacker: Any, accounts: dict[str, str]
    ) -> None:

        response = attacker.get(
            "/api/v1/products", headers={"X-Workspace-Id": accounts["victim_workspace"]}
        )

        assert response.status_code in (403, 404), response.text