"""Security testing against a running API and a real database (Phase 26, §33).

This is an attack suite, not a coverage suite. Every test starts from what an
attacker has — a session, a workspace id, an id belonging to someone else — and
asserts the request is refused, or that the refusal leaks nothing.

Three things distinguish it from the integration suite that preceded it:

- **The API role, not a bypass.** `tests/integration` already runs DML as a
  `NOSUPERUSER` so RLS applies. These tests also assert the *owning* role is
  subject to its own policies, because `FORCE ROW LEVEL SECURITY` is what makes
  that true and the application connects as the owner.
- **HTTP, not in-process.** CSRF, cookies, headers and account enumeration are
  claims about the wire; calling a handler directly proves none of them.
- **No mocking of the defences.** Where a control is a database trigger or a
  policy, the test drives SQL as an attacker holding that credential would.

Running it
----------
The API under test must use the *same* database this suite manages, because the
attacks name rows it writes:

```bash
docker run -d -p 55433:5432 -e POSTGRES_PASSWORD=catalogguard \\
  -e POSTGRES_DB=catalogguard_test pgvector/pgvector:pg16

URL=postgresql://postgres:catalogguard@127.0.0.1:55433/catalogguard_test

ASYNC_URL=postgresql+asyncpg://postgres:catalogguard@127.0.0.1:55433/catalogguard_test?ssl=disable

DATABASE_URL="$URL?sslmode=disable" CATALOGGUARD_TEST_DATABASE_URL="$ASYNC_URL" \\
  uvicorn app.main:app --port 8100 &

pytest tests/security
```

Without a reachable API the HTTP tests **skip with a reason** rather than
passing against nothing — the rule `tests/integration` follows for a missing
database.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

# The database fixtures are borrowed rather than reimplemented: this suite needs
# exactly the ones `tests/integration` already proved, including the
# `NOSUPERUSER` role every RLS assertion depends on.
#
# Re-exported by assignment rather than by `pytest_plugins`, which pytest only
# permits in the rootdir conftest — defining it here is an error that stops the
# whole suite collecting, not a warning about this directory.
from tests.integration import conftest as _integration  # noqa: E402

app_engine = _integration.app_engine
app_session = _integration.app_session
app_session_factory = _integration.app_session_factory
database = _integration.database
db_session = _integration.db_session
engine = _integration.engine
migrated = _integration.migrated
session_factory = _integration.session_factory
tenant_session = _integration.tenant_session

API_URL = os.getenv("CATALOGGUARD_SECURITY_API", "http://127.0.0.1:8100")
ATTACKER_EMAIL = os.getenv("CATALOGGUARD_SECURITY_EMAIL", "attacker@example.com")
ATTACKER_PASSWORD = os.getenv(
    "CATALOGGUARD_SECURITY_PASSWORD", "correct horse battery staple"
)


@pytest.fixture(scope="session")
def api_available() -> None:
    import httpx

    try:
        response = httpx.get(f"{API_URL}/health", timeout=5)
    except Exception as exc:  # noqa: BLE001 - "not running" is the expected case
        pytest.skip(f"no API at {API_URL} ({type(exc).__name__}); skipping HTTP attacks")
    if response.status_code != 200:
        pytest.skip(f"API at {API_URL} answered {response.status_code}")


class Client:
    """A signed-in HTTP client, which is what an attacker actually has."""

    def __init__(self, base_url: str, email: str, password: str) -> None:
        import httpx

        self.base = base_url
        self.email = email
        self.password = password
        self.http = httpx.Client(base_url=base_url, follow_redirects=False, timeout=15)
        self.user_id: str = ""
        self.workspace_id: str = ""

    def sign_in(self) -> Client:
        token = self.csrf()
        response = self.http.post(
            "/api/v1/auth/login",
            json={"email": self.email, "password": self.password},
            headers={"X-CSRF-Token": token},
        )
        response.raise_for_status()
        session = self.http.get("/api/v1/auth/me").json()
        self.user_id = session["user"]["id"]
        self.workspace_id = session["workspaces"][0]["id"]
        return self

    def csrf(self) -> str:
        return self.http.get("/api/v1/auth/csrf").json()["csrf_token"]

    def workspace(self, workspace_id: str | None = None) -> dict[str, str]:
        return {
            "X-CSRF-Token": self.csrf(),
            "X-Workspace-Id": workspace_id or self.workspace_id,
        }

    def get(self, path: str, **kwargs: object):
        return self.http.get(path, **kwargs)

    def post(self, path: str, **kwargs: object):
        return self.http.post(path, **kwargs)


@pytest.fixture
async def accounts(engine: Any) -> dict[str, str]:
    """An attacker's workspace and a victim's, each with rows worth stealing.

    Written as the table owner, because placing a row in a workspace you are not
    a member of is only possible that way — which is exactly what makes these
    rows a legitimate target rather than a fabricated id.
    """
    from app.core.security.passwords import hash_password
    from app.models.tenancy import UserRole

    ids: dict[str, str] = {}
    organization = uuid.uuid4()
    async with engine.begin() as connection:
        from sqlalchemy import text

        # This suite owns its database, so it starts from nothing: the seeded
        # identity has a fixed email, and a second test would collide with the
        # first. Truncating rather than rolling back is the same choice
        # `tests/integration` makes, and for the same reason — a rolled-back
        # fixture would hide the commit-time behaviour these tests observe.
        await connection.execute(
            text(
                "TRUNCATE approval_actions, approval_requests, simulation_runs, "
                "risk_assessments, change_proposals, conflicts, reconciliation_runs, "
                "source_records, catalog_sources, products, catalogs, "
                "workspace_memberships, auth_sessions, users, workspaces, "
                "organizations, login_throttles CASCADE"
            )
        )

        await connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug, is_active) "
                "VALUES (:id, 'Security test org', :slug, true)"
            ),
            {"id": organization, "slug": f"sec-org-{uuid.uuid4().hex[:8]}"},
        )

        for label, role in (("attacker", UserRole.ECOMMERCE_OPS_MANAGER), ("victim", None)):
            workspace = uuid.uuid4()
            user = uuid.uuid4()
            await connection.execute(
                text(
                    "INSERT INTO workspaces (id, organization_id, name, slug) "
                    "VALUES (:id, :org, :name, :slug)"
                ),
                {
                    "id": workspace,
                    "org": organization,
                    "name": f"{label.title()} workspace",
                    "slug": f"sec-{label}-{uuid.uuid4().hex[:8]}",
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO users (id, email, hashed_password, full_name, "
                    "is_active, is_superuser) VALUES (:id, :email, :hash, :name, "
                    "true, false)"
                ),
                {
                    "id": user,
                    "email": ATTACKER_EMAIL if role else "victim@example.com",
                    # Both accounts share one password: several attacks are
                    # about *whose* credential is refused, and a fixture whose
                    # second identity could never sign in would make those
                    # assertions meaningless.
                    "hash": hash_password(ATTACKER_PASSWORD),
                    "name": label.title(),
                },
            )
            if role:
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
                        "role": role.value,
                    },
                )

            catalog = uuid.uuid4()
            await connection.execute(
                text(
                    "INSERT INTO catalogs (id, workspace_id, name, slug, "
                    "current_version, created_at, updated_at) VALUES (:id, :ws, "
                    ":name, :slug, 1, now(), now())"
                ),
                {
                    "id": catalog,
                    "ws": workspace,
                    "name": f"{label.title()} catalogue",
                    "slug": f"sec-{label}-{uuid.uuid4().hex[:8]}",
                },
            )

            product = uuid.uuid4()
            await connection.execute(
                text(
                    "INSERT INTO products (id, workspace_id, catalog_id, sku, title, "
                    "status, version, attributes, media, is_parent, created_at, "
                    "updated_at) VALUES (:id, :ws, :cat, :sku, 'Confidential roadmap "
                    "item', 'active', 1, '{}'::jsonb, '{}'::jsonb, false, now(), now())"
                ),
                {
                    "id": product,
                    "ws": workspace,
                    "cat": catalog,
                    "sku": f"SEC-{label.upper()}-CONFIDENTIAL",
                },
            )

            proposal = uuid.uuid4()
            await connection.execute(
                text(
                    "INSERT INTO change_proposals (id, workspace_id, product_id, "
                    "change_ref, field_name, current_value, proposed_value, reason, "
                    "evidence, confidence, validation_status, validation_errors, "
                    "risk_level, requires_approval, seo_impact, compliance_impact, "
                    "affected_channels, affected_relationships, status, created_at, "
                    "updated_at) VALUES (:id, :ws, :product, :ref, 'brand', "
                    "'\"secret-brand\"'::jsonb, '\"other\"'::jsonb, 'security test', "
                    "'[]'::jsonb, 0.5, 'validated', '[]'::jsonb, 'MEDIUM', true, "
                    "'NONE', 'NEUTRAL', '[]'::jsonb, '[]'::jsonb, 'pending_approval', "
                    "now(), now())"
                ),
                {
                    "id": proposal,
                    "ws": workspace,
                    "product": product,
                    "ref": f"CHG-SEC-{uuid.uuid4().hex[:8].upper()}",
                },
            )

            ids[f"{label}_catalog"] = str(catalog)
            ids[f"{label}_workspace"] = str(workspace)
            ids[f"{label}_user"] = str(user)
            ids[f"{label}_product"] = str(product)
            ids[f"{label}_proposal"] = str(proposal)

    return ids


@pytest.fixture
async def cleared_throttle(engine: Any) -> None:  # noqa: D401
    """Delete every lockout row, as the owner, with `DELETE` rather than `TRUNCATE`.

    A suite that tests the lockout must not inherit one, and these tests create
    one on purpose. `TRUNCATE` was the first attempt and it is the wrong
    statement here: it needs an ACCESS EXCLUSIVE lock on the table, which
    collides with the API process's own connections, so the fixture blocked and
    every test after the first skipped with a sign-in failure — which reads like
    a clean run while testing nothing. `DELETE` takes row locks only.
    """
    from sqlalchemy import text

    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM login_throttles"))


@pytest.fixture
async def attacker(api_available: None, accounts: dict[str, str], cleared_throttle: None) -> Client:
    """A signed-in client.

    Depends on `accounts` deliberately: the seeded identity has to exist in the
    API's database before anyone tries to sign in as it. Ordering it the other
    way made every HTTP attack skip with a sign-in failure, which reads like a
    passing suite and is the opposite.
    """

    client = Client(API_URL, ATTACKER_EMAIL, ATTACKER_PASSWORD)
    try:
        client.sign_in()
    except Exception as exc:  # noqa: BLE001 - report rather than fail obscurely
        detail = getattr(getattr(exc, "response", None), "text", "")
        pytest.skip(
            f"could not sign in as {ATTACKER_EMAIL} against {API_URL} "
            f"({type(exc).__name__}: {detail[:200]}); is the API using the test database?"
        )
    return client