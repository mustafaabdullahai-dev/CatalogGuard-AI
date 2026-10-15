"""The §33 controls that are claims until something asserts them.

Cross-tenant isolation, CSRF, enumeration, throttling and prompt injection have
their own files. This one covers the rest of §33's list, and it does not pretend
all sixteen items are equally real: some are implemented and assertable, some
are *structurally* safe today because the feature that would need them has not
been built, and one — outbound requests — is asserted as a property of the code
rather than of a test.

The distinction is the point. A security checklist that marks "SSRF protection"
done because no feature makes outbound requests yet is a false assurance; the
honest statement is "no request-driven URL, so no surface — re-check when a live
connector lands".
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


class TestMarketplaceCredentialsAreEncryptedAtRest:
    """§33: "Marketplace credentials must never be stored as plaintext"."""

    def test_credentials_round_trip_through_encryption(self) -> None:
        from app.connectors.credentials import decrypt_credentials, encrypt_credentials

        plaintext = {"api_key": "super-secret-value", "shop": "northwind.myshopify.com"}
        encrypted = encrypt_credentials(plaintext)

        assert encrypted != plaintext
        assert "super-secret-value" not in encrypted
        assert decrypt_credentials(encrypted) == plaintext

    def test_the_encrypted_blob_is_not_the_plaintext(self) -> None:
        """A reversible-looking value would mean encryption did not happen."""

        from app.connectors.credentials import encrypt_credentials

        encrypted = encrypt_credentials({"api_key": "abc"})

        assert "abc" not in encrypted
        assert len(encrypted) > 20, "a Fernet token, not the input"

    async def test_no_credential_is_reachable_over_http(
        self, attacker: Any, engine: Any
    ) -> None:
        """No route returns a marketplace credential, to anyone.

        Asserted by hitting the endpoints that exist and looking for anything
        that looks like a secret, rather than by reading the route table: the
        question is what an authenticated caller can obtain, not what the code
        appears to offer.
        """

        endpoints = [
            "/api/v1/catalogs",
            "/api/v1/products?limit=5",
            "/api/v1/catalog-sources",
            "/api/v1/conflicts",
            "/api/v1/change-proposals",
            "/api/v1/approvals",
            "/api/v1/reconciliation-runs",
            "/api/v1/compliance/documents",
            "/api/v1/observability/summary",
            "/api/v1/auth/me",
        ]
        forbidden = {
            "encrypted_credentials",
            "credentials",
            "api_key",
            "access_token",
            "refresh_token",
            "client_secret",
            "password",
            "hashed_password",
        }

        def keys_in(node: object) -> set[str]:
            if isinstance(node, dict):
                return {k.lower() for k in node} | set().union(
                    *(keys_in(v) for v in node.values()), set()
                ) if node else set()
            if isinstance(node, list):
                return set().union(*(keys_in(v) for v in node), set()) if node else set()
            return set()

        for path in endpoints:
            response = attacker.get(
                path, headers={"X-Workspace-Id": attacker.workspace_id}
            )
            leaked = keys_in(response.json()) & forbidden
            assert not leaked, f"{path} exposes {sorted(leaked)}"


class TestSecretManagement:
    """§33: "secret management". The application refuses to start with one."""

    def test_a_placeholder_secret_is_refused(self) -> None:
        """Already true since Phase 4; asserted here because §33 lists it."""

        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError):
            Settings(
                QROQ_API_KEY="REPLACE_ME",
                JWT_SECRET="REPLACE_ME",
                ENCRYPTION_KEY="k56RU-1GjWszzDdaaTRbNKnrgc3yq-ph8dHk_eVVLPg=",
                DATABASE_URL="postgresql://u:p@h/db?sslmode=require",
            )

    def test_an_embedding_mode_without_a_model_is_refused(self) -> None:
        """A semantic mode that cannot produce vectors fails at first ingest.

        Named here because it is the same class of control: configuration that
        looks load-bearing and is not, discovered at the worst moment.
        """

        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError):
            Settings(
                QROQ_API_KEY="a-real-looking-key-value",
                ENCRYPTION_KEY="k56RU-1GjWszzDdaaTRbNKnrgc3yq-ph8dHk_eVVLPg=",
                DATABASE_URL="postgresql://u:p@h/db?sslmode=require",
                RAG_EMBEDDING_MODE="qroq",
                QROQ_EMBEDDING_MODEL="",
            )

    def test_an_invalid_encryption_key_is_refused(self) -> None:
        """A key that is not a Fernet key would fail at first credential write,
        silently, in production."""

        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError):
            Settings(
                QROQ_API_KEY="a-real-looking-key-value",
                ENCRYPTION_KEY="not-a-fernet-key",
                DATABASE_URL="postgresql://u:p@h/db?sslmode=require",
            )


class TestOutboundRequestsAreConfigurationOnly:
    """§33's SSRF item, stated precisely rather than checked off.

    An SSRF defence is needed when a **request** can influence an outbound URL.
    Today nothing can: the only outbound calls go to the LLM endpoint and the
    embedding endpoint, both from configuration, and the only marketplace
    connector is a mock that opens no socket.

    So this asserts the *structural* property — the surface is configuration —
    and names the trigger for revisiting it.
    """

    def test_no_route_accepts_a_url_that_is_about_to_be_fetched(self) -> None:
        """No API schema carries a field shaped like a fetchable endpoint.

        Searched in the served OpenAPI document rather than the source, because
        what matters is what a caller can supply.
        """
        from app.main import app

        spec = app.openapi()
        suspicious = {"url", "webhook_url", "callback_url", "endpoint", "base_url", "host"}

        offenders: list[str] = []
        for name, schema in spec.get("components", {}).get("schemas", {}).items():
            for field in (schema.get("properties") or {}):
                if field.lower() in suspicious:
                    offenders.append(f"{name}.{field}")

        assert not offenders, (
            f"request schemas carry a fetchable URL field: {offenders}. SSRF "
            "defences are needed for any of these."
        )

    def test_the_only_marketplace_connector_opens_no_socket(self) -> None:
        """D-005's registry holds exactly one adapter, and it is the mock.

        Asserted through the factory rather than through a registry object: the
        connector is resolved per marketplace, so the question is what that
        call can hand back.
        """

        from app.connectors.base import UnknownMarketplaceError
        from app.connectors.mock import MockConnector
        from app.connectors.registry import connector_for

        connector = connector_for("mock", {"api_key": "x"})

        assert isinstance(connector, MockConnector), (
            f"{type(connector).__name__} is a live adapter; SSRF protection and "
            "outbound TLS are now a real requirement"
        )

        # An unsupported marketplace must raise rather than silently resolve to
        # something that could make a real call.
        # Any real marketplace raises: there is no adapter to fall through to.
        with pytest.raises(UnknownMarketplaceError):
            connector_for("amazon", {"api_key": "x"})


class TestAuditIntegrityUnderAnApplicationCredential:
    """§33's "audit logs", asserted as D-013 claims: immutable in the database.

    The unit suite asserts the trigger exists. This asserts it fires for the role
    the application actually connects as, which is the only assertion that
    matters for "the trail cannot be edited".
    """

    async def test_the_application_role_cannot_update_an_audit_row(
        self, app_session: Any, app_session_factory: Any
    ) -> None:
        from sqlalchemy.exc import DBAPIError

        from app.models.reconciliation import AuditLog

        entry = AuditLog(
            workspace_id=None,
            entity_type="change_proposal",
            entity_id="00000000-0000-0000-0000-000000000001",
            action="proposal.created",
            old_value=None,
            new_value=None,
            payload_hash="0" * 64,
        )
        async with app_session_factory() as session:
            # The row itself needs a bound tenant to exist; the refusal below is
            # about the UPDATE, which no binding can license.
            from uuid import uuid4

            from app.db.session import bind_tenant

            workspace = uuid4()
            await bind_tenant(session, workspace)
            entry.workspace_id = workspace
            session.add(entry)
            await session.commit()
            entry_id = entry.id

        async with app_session_factory() as session:
            from uuid import uuid4

            from app.db.session import bind_tenant

            await bind_tenant(session, entry.workspace_id)
            with pytest.raises(DBAPIError):
                await session.execute(
                    text("UPDATE audit_logs SET action = 'tampered' WHERE id = :id"),
                    {"id": entry_id},
                )

    async def test_the_application_role_cannot_delete_an_audit_row(
        self, app_session_factory: Any, engine: Any
    ) -> None:
        from sqlalchemy.exc import DBAPIError

        from app.models.reconciliation import AuditLog

        entry = AuditLog(
            workspace_id=None,
            entity_type="change_proposal",
            entity_id="00000000-0000-0000-0000-000000000001",
            action="proposal.created",
            old_value=None,
            new_value=None,
            payload_hash="0" * 64,
        )
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE audit_logs CASCADE"))
        async with app_session_factory() as session:
            from uuid import uuid4

            from app.db.session import bind_tenant

            workspace = uuid4()
            await bind_tenant(session, workspace)
            entry.workspace_id = workspace
            session.add(entry)
            await session.commit()
            entry_id = entry.id

        async with app_session_factory() as session:
            from app.db.session import bind_tenant

            await bind_tenant(session, entry.workspace_id)
            with pytest.raises(DBAPIError):
                await session.execute(
                    text("DELETE FROM audit_logs WHERE id = :id"), {"id": entry_id}
                )


class TestResponseSizeIsBounded:
    """§33's "output validation": a list endpoint must not return everything."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/products?limit=100000",
            "/api/v1/change-proposals?limit=100000",
            "/api/v1/compliance/search?q=label&top_k=1000",
        ],
    )
    def test_an_oversized_limit_is_refused_or_capped(
        self, attacker: Any, path: str
    ) -> None:
        """Unbounded output is a denial of service with a valid session.

        Either the API rejects the number or it caps it. What it must not do is
        honour it.
        """
        response = attacker.get(path, headers={"X-Workspace-Id": attacker.workspace_id})

        assert response.status_code < 500, response.text
        if response.status_code == 200:
            body = response.json()
            collection = next(
                (value for value in body.values() if isinstance(value, list)), []
            )
            assert len(collection) <= 200, (
                f"{path} returned {len(collection)} rows; the cap is not enforced"
            )


class TestSecretsNeverAppearInLogsOrErrors:
    """D-010's masking, asserted where a leak would actually happen."""

    def test_a_failed_login_does_not_log_the_password(
        self, attacker: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Synchronous on purpose: the point is the API's logging, and a sync
        client keeps the event loop free of this test's own noise."""

        import httpx

        client = httpx.Client(base_url=attacker.base, timeout=15)
        token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        client.post(
            "/api/v1/auth/login",
            json={"email": attacker.email, "password": "SENTINEL-PASSWORD-VALUE"},
            headers={"X-CSRF-Token": token},
        )
        client.close()

        import logging

        captured = "\n".join(
            record.getMessage()
            for record in caplog.records
            if isinstance(record, logging.LogRecord)
        )
        assert "SENTINEL-PASSWORD-VALUE" not in captured

    def test_a_validation_error_does_not_reflect_the_input(
        self, attacker: Any
    ) -> None:
        """What a caller gets back must not carry their input verbatim.

        A validation message that echoes the submitted value turns every endpoint
        into a reflector, and a hostile one reflecting markup or script is a
        reflected-XSS vector if any surface ever renders it.
        """
        import httpx

        sentinel = "REFLECTED-<script>alert(1)</script>"
        client = httpx.Client(base_url=attacker.base, timeout=15)
        token = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        response = client.post(
            "/api/v1/auth/login",
            json={"email": sentinel, "password": sentinel},
            headers={"X-CSRF-Token": token},
        )
        client.close()

        assert response.status_code in (401, 422), response.text
        assert sentinel not in response.text
        assert "<script>" not in response.text
        assert "Traceback" not in response.text