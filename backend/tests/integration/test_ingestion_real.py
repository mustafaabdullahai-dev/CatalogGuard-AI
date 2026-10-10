"""A file upload, end to end, against a real database (Phase 24, §40).

The §69 chain test seeds `source_records` directly. That skips the half of
ingestion most likely to be wrong: the multipart request, the file validation,
the parser, and the write that follows. This file uploads bytes over HTTP and
then reads the rows back with SQL, so the assertion is about what the database
holds rather than what the response claimed.

Authorization is real too — the request carries a session cookie and a
`X-Workspace-Id`, resolved by the actual dependency chain rather than an
override.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.security.passwords import hash_password
from app.db.session import get_session, get_session_no_commit
from app.main import app
from app.models.tenancy import UserRole

PREFIX = "/api/v1"
PASSWORD = "correct horse battery staple"

# Two rows, one with a conflict waiting to happen: the same SKU in the same
# field, two values. §69's scenario is built from a file much like this.
CSV_BODY = (
    "sku,title,color,price\n"
    "SKU-100,Navy Runner,navy,89.00\n"
    "SKU-100,Navy Runner,navy blue,92.50\n"
    "SKU-200,Canvas Tote,beige,24.00\n"
)


def _csv_file() -> tuple[str, bytes, str]:
    return ("catalog.csv", CSV_BODY.encode("utf-8"), "text/csv")


@pytest_asyncio.fixture
async def signed_in_client(
    engine: Any, app_session_factory: async_sessionmaker[AsyncSession]
) -> Any:
    """A client whose session belongs to a real member of a real workspace.

    Rather than override `get_current_tenant` — which is what the unit suite
    does, and which would mean this file tests nothing about authorization —
    the user, membership, workspace and session are all written to the database
    and the real dependency chain resolves them from the cookie.
    """
    settings = get_settings()
    workspace_id, user_id = uuid4(), uuid4()

    # `login_throttles` is in this list because D-014's lockout is a real
    # database row: a test run that produces a few 401s locks the next run's
    # sign-in, which is the throttle working exactly as designed.
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE source_records, catalog_sources, workspace_memberships, "
                "auth_sessions, users, workspaces, organizations, login_throttles "
                "CASCADE"
            )
        )

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug, is_active) "
                "VALUES (:id, 'Acme', :slug, true)"
            ),
            {"id": uuid4(), "slug": f"org-{uuid4().hex[:8]}"},
        )
        organization = (
            await connection.execute(text("SELECT id FROM organizations LIMIT 1"))
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO workspaces (id, organization_id, name, slug) "
                "VALUES (:id, :org, 'Catalog', :slug)"
            ),
            {"id": workspace_id, "org": organization, "slug": f"ws-{uuid4().hex[:8]}"},
        )
        await connection.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, full_name, "
                "is_active, is_superuser) VALUES (:id, 'owner@example.com', "
                ":hash, 'Owner', true, false)"
            ),
            {"id": user_id, "hash": hash_password(PASSWORD)},
        )
        await connection.execute(
            text(
                "INSERT INTO workspace_memberships (id, workspace_id, user_id, role, "
                "created_at, updated_at) VALUES (:id, :ws, :user, :role, now(), now())"
            ),
            {
                "id": uuid4(),
                "ws": workspace_id,
                "user": user_id,
                "role": UserRole.ECOMMERCE_OPS_MANAGER.value,
            },
        )

    async def _session():
        async with app_session_factory() as session:
            try:
                yield session
            finally:
                await session.rollback()

    # Both session dependencies: login reads through `get_session_no_commit`,
    # so overriding only one would send half the flow to the configured
    # production URL.
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_session_no_commit] = _session
    client = httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="https://test", follow_redirects=False
    )
    try:
        await client.get(f"{PREFIX}/auth/csrf")
        response = await client.post(
            f"{PREFIX}/auth/login",
            json={"email": "owner@example.com", "password": PASSWORD},
            headers={
                settings.csrf_header_name: client.cookies.get(settings.csrf_cookie_name) or ""
            },
        )
        assert response.status_code == 200, response.text
        yield client, workspace_id
    finally:
        app.dependency_overrides.clear()
        await client.aclose()


async def _create_source(
    engine: Any, client: httpx.AsyncClient, workspace_id: Any, settings: Any
) -> Any:
    """Create a catalog source over the API, as the operator would."""

    response = await client.post(
        f"{PREFIX}/catalog-sources",
        json={"name": "Supplier feed", "source_type": "manufacturer"},
        headers={
            settings.csrf_header_name: client.cookies.get(settings.csrf_cookie_name) or "",
            "X-Workspace-Id": str(workspace_id),
        },
    )
    assert response.status_code in (200, 201), response.text

    return response.json()["id"]


def _headers(client: httpx.AsyncClient, workspace_id: Any) -> dict[str, str]:
    settings = get_settings()

    return {
        settings.csrf_header_name: client.cookies.get(settings.csrf_cookie_name) or "",
        "X-Workspace-Id": str(workspace_id),
    }


@pytest.mark.integration
class TestUploadAgainstARealDatabase:
    async def test_an_uploaded_csv_becomes_source_records(
        self, signed_in_client: Any, engine: Any
    ) -> None:
        client, workspace_id = signed_in_client
        settings = get_settings()
        source_id = await _create_source(engine, client, workspace_id, settings)
        filename, content, content_type = _csv_file()

        response = await client.post(
            f"{PREFIX}/catalog-sources/{source_id}/upload",
            files={"file": (filename, content, content_type)},
            headers=_headers(client, workspace_id),
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["records_inserted"] == 3
        assert body["format"] == "csv"

        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT raw_data->>'sku' AS sku FROM source_records "
                        "WHERE workspace_id = :ws ORDER BY raw_data->>'sku'"
                    ),
                    {"ws": workspace_id},
                )
            ).all()

        assert len(rows) == 3
        assert {row.sku for row in rows} == {"SKU-100", "SKU-200"}

    async def test_the_stored_payload_is_raw_and_not_normalized(
        self, signed_in_client: Any, engine: Any
    ) -> None:
        """D-018: ingestion stores what arrived, untouched.

        The two `SKU-100` rows disagree about colour. If ingestion normalized
        them, the conflict §69's reconciliation depends on would have been
        resolved before the reconciler ever saw it.
        """
        client, workspace_id = signed_in_client
        settings = get_settings()
        source_id = await _create_source(engine, client, workspace_id, settings)
        filename, content, content_type = _csv_file()
        await client.post(
            f"{PREFIX}/catalog-sources/{source_id}/upload",
            files={"file": (filename, content, content_type)},
            headers=_headers(client, workspace_id),
        )

        async with engine.connect() as connection:
            payloads = (
                await connection.execute(
                    text(
                        "SELECT raw_data->>'color' AS color FROM source_records "
                        "WHERE workspace_id = :ws AND raw_data->>'sku' = 'SKU-100'"
                    ),
                    {"ws": workspace_id},
                )
            ).scalars().all()

        assert sorted(payloads) == ["navy", "navy blue"]

    async def test_a_rejected_file_stores_nothing(
        self, signed_in_client: Any, engine: Any
    ) -> None:
        client, workspace_id = signed_in_client
        settings = get_settings()
        source_id = await _create_source(engine, client, workspace_id, settings)

        response = await client.post(
            f"{PREFIX}/catalog-sources/{source_id}/upload",
            files={"file": ("payload.exe", b"MZ\x90\x00", "application/octet-stream")},
            headers=_headers(client, workspace_id),
        )

        # 415 rather than 422: the file is a supported request carrying an
        # unsupported payload, which is what the status means.
        assert response.status_code == 415, response.text
        async with engine.connect() as connection:
            count = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM source_records WHERE workspace_id = :ws"
                    ),
                    {"ws": workspace_id},
                )
            ).scalar_one()

        assert count == 0

    async def test_another_workspaces_source_is_not_found(
        self, signed_in_client: Any, engine: Any
    ) -> None:
        """D-011 through the API: a real id from another tenant is a 404."""

        client, workspace_id = signed_in_client
        stranger = uuid4()

        async with engine.begin() as connection:
            organization = (
                await connection.execute(text("SELECT id FROM organizations LIMIT 1"))
            ).scalar_one()
            await connection.execute(
                text(
                    "INSERT INTO workspaces (id, organization_id, name, slug) "
                    "VALUES (:id, :org, 'Theirs', :slug)"
                ),
                {"id": stranger, "org": organization, "slug": f"ws-{uuid4().hex[:8]}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO catalog_sources (id, workspace_id, name, "
                    "source_type, detected_schema, authorization_context, "
                    "created_at, updated_at) VALUES (:id, :ws, 'Theirs', "
                    "'manufacturer', '{}'::jsonb, '{}'::jsonb, now(), now())"
                ),
                {"id": uuid4(), "ws": stranger},
            )
            foreign_id = (
                await connection.execute(
                    text("SELECT id FROM catalog_sources WHERE workspace_id = :ws"),
                    {"ws": stranger},
                )
            ).scalar_one()

        response = await client.post(
            f"{PREFIX}/catalog-sources/{foreign_id}/upload",
            files={"file": _csv_file()},
            headers=_headers(client, workspace_id),
        )

        assert response.status_code == 404