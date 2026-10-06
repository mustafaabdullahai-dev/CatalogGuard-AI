"""Compliance knowledge endpoints (Phase 13, §13, §14).

The database is mocked and the HTTP layer is real, matching the D-014 endpoint
suite. What is under test here is the wire contract: the routes exist, they
require a session, the capability gate actually rejects a role that cannot
write, and — the part unique to this router — that the `note` field never claims
more than the retrieval signals that fired.

That last point is the honesty claim in §37 applied to evidence. A deployment
running without an embedding provider returns keyword matches, and a response
that says nothing would let the caller read them as semantic relevance. The
service layer already reports which signals produced a ranking; these tests pin
the fact that the HTTP layer surfaces that distinction instead of smoothing it
away.

Nothing here asserts that a hit is *correct* compliance advice. §16 and §5 make
risk and validity deterministic and D-005 rules out a rule-cited compliance
engine in the MVP, so a 200 on `/compliance/search` proves evidence was
retrieved, not that a change is safe.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport

from app.api.deps import AuthContext, TenantScope, get_auth_context, get_current_tenant
from app.core.config import get_settings
from app.db.base import TenantContext
from app.db.session import get_session
from app.main import app
from app.models.tenancy import User, UserRole
from app.rag import service as rag_service

PREFIX = "/api/v1"
DOCUMENTS = f"{PREFIX}/compliance/documents"
SEARCH = f"{PREFIX}/compliance/search"

WORKSPACE = uuid.uuid4()

DOCUMENT_PAYLOAD: dict[str, Any] = {
    "title": "Amazon Apparel Title Requirements",
    "content": (
        "# Title requirements\n\n"
        "Product titles must not exceed 200 characters and must include the "
        "brand name. Section 12.3 governs variation themes.\n"
    ),
    "marketplace_code": "amazon",
    "country_code": "US",
}


def _user() -> User:
    return User(
        id=uuid.uuid4(),
        email="ops@example.com",
        hashed_password="$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA",
        full_name="Ops Person",
        is_active=True,
    )


def _session() -> MagicMock:
    """A session whose `execute` returns empty results and accepts commits.

    The list route issues a count, a page query, and one grouped count. With no
    database, all three yield nothing and the route degrades to "empty corpus",
    which is a legitimate response and the one these tests assert.
    """
    session = MagicMock()
    empty = MagicMock()
    empty.scalar_one_or_none.return_value = 0
    empty.all.return_value = []
    empty.scalars.return_value = empty
    session.execute = AsyncMock(return_value=empty)
    session.commit = AsyncMock(return_value=None)
    session.rollback = AsyncMock(return_value=None)
    return session


def _override(role: str) -> dict[Any, Any]:
    user = _user()
    session = MagicMock()
    session.id = uuid.uuid4()
    session.user_id = user.id
    context = AuthContext(user=user, session=session, memberships=())
    scope = TenantScope(
        context=context,
        tenant=TenantContext(workspace_id=WORKSPACE, user_id=user.id),
        role=role,
    )
    # `get_session` is overridden per request so a test that needs a different
    # result can replace it again without this dict holding a stale mock.
    return {
        get_auth_context: lambda: context,
        get_current_tenant: lambda: scope,
        get_session: lambda: _session(),
    }


@pytest.fixture
def csrf(client: httpx.AsyncClient):
    """Signed double-submit token; the POST route is not exempt (CSRF-002)."""

    async def _token() -> dict[str, str]:
        await client.get(f"{PREFIX}/auth/csrf")
        token = client.cookies.get(get_settings().csrf_cookie_name)
        assert token is not None, "no CSRF cookie was issued"
        return {get_settings().csrf_header_name: token}

    return _token


@pytest.fixture
def client() -> httpx.AsyncClient:
    """`https://` so the Secure CSRF cookie is actually sent back.

    The cookie is `Secure` (see `CSRFMiddleware`), and httpx's cookie jar drops
    secure cookies on a plain http request — which would make every POST fail
    double-submit validation for a reason unrelated to what is being tested.
    """
    return httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://test",
        follow_redirects=False,
    )


class TestComplianceApi:
    async def test_routes_are_in_the_spec(self) -> None:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            paths = (await http.get("/openapi.json")).json()["paths"]
        assert DOCUMENTS in paths
        assert SEARCH in paths
        assert "post" in paths[DOCUMENTS]
        assert "get" in paths[DOCUMENTS]
        assert "get" in paths[SEARCH]

    async def test_every_route_requires_a_session(
        self, client: httpx.AsyncClient, csrf
    ) -> None:
        """No override: an unauthenticated caller gets 401, never 500.

        The authentication dependency runs before any database access, so this
        holds even with Postgres unreachable.
        """
        response = await client.get(SEARCH, params={"q": "title limit"})
        assert response.status_code == 401

        response = await client.get(DOCUMENTS)
        assert response.status_code == 401

        # POST without a session: 401 from auth, or 403 from the CSRF layer
        # rejecting a caller that never fetched a token. Neither is a 500, and
        # neither is a 201, which is the property that matters.
        response = await client.post(DOCUMENTS, json=DOCUMENT_PAYLOAD)
        assert response.status_code in (401, 403)

    async def test_roles_without_write_are_refused(
        self, client: httpx.AsyncClient, csrf
    ) -> None:
        """`write` is the gate (OPEN DECISION on `require_compliance_authority`).

        Both roles below hold `read` but not `write`, so a 403 here proves the
        gate is the capability matrix rather than mere authentication.
        """
        for role in (
            UserRole.SEO_SPECIALIST.value,
            UserRole.DATA_INTEGRATION_ADMIN.value,
        ):
            app.dependency_overrides.update(_override(role))
            try:
                response = await client.get(
                    SEARCH,
                    params={"q": "title limit"},
                    headers={"X-Workspace-Id": str(WORKSPACE)},
                )
                assert response.status_code == 403, role

                response = await client.post(
                    DOCUMENTS,
                    json=DOCUMENT_PAYLOAD,
                    headers={**await csrf(), "X-Workspace-Id": str(WORKSPACE)},
                )
                assert response.status_code == 403, role
            finally:
                app.dependency_overrides.clear()

    async def test_writer_can_list_an_empty_corpus(
        self, client: httpx.AsyncClient
    ) -> None:
        app.dependency_overrides.update(_override(UserRole.DATA_STEWARD.value))
        try:
            response = await client.get(
                DOCUMENTS, headers={"X-Workspace-Id": str(WORKSPACE)}
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        body = response.json()
        assert body["documents"] == []
        assert body["total"] == 0

    async def test_search_requires_a_query(
        self, client: httpx.AsyncClient
    ) -> None:
        """Validation still fires when authentication succeeds.

        `min_length=2` keeps a single character from reaching `websearch_to_tsquery`,
        where it is a legitimate but useless query the caller would have to pay a
        round trip to discover was empty.
        """
        app.dependency_overrides.update(_override(UserRole.DATA_STEWARD.value))
        try:
            response = await client.get(
                SEARCH,
                params={"q": "x"},
                headers={"X-Workspace-Id": str(WORKSPACE)},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422

    async def test_top_k_is_bounded(
        self, client: httpx.AsyncClient
    ) -> None:
        app.dependency_overrides.update(_override(UserRole.DATA_STEWARD.value))
        try:
            response = await client.get(
                SEARCH,
                params={"q": "title limit", "top_k": 51},
                headers={"X-Workspace-Id": str(WORKSPACE)},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422


class TestIngestNote:
    """The `note` is the honest status line, not a success banner."""

    async def test_unindexed_corpus_says_so(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app.dependency_overrides.update(_override(UserRole.DATA_STEWARD.value))
        result = rag_service.IngestResult(
            document_id=uuid.uuid4(),
            chunks_written=3,
            token_total=410,
            indexed_semantically=False,
            duplicate=False,
        )
        monkeypatch.setattr(rag_service, "ingest_document", AsyncMock(return_value=result))
        try:
            response = await client.post(
                DOCUMENTS,
                json=DOCUMENT_PAYLOAD,
                headers={**await csrf(), "X-Workspace-Id": str(WORKSPACE)},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 201
        body = response.json()
        assert body["indexed_semantically"] is False
        assert "WITHOUT vectors" in body["note"]
        assert "keyword and metadata evidence only" in body["note"]

    async def test_semantic_index_says_hybrid(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app.dependency_overrides.update(_override(UserRole.DATA_STEWARD.value))
        result = rag_service.IngestResult(
            document_id=uuid.uuid4(),
            chunks_written=3,
            token_total=410,
            indexed_semantically=True,
            duplicate=False,
        )
        monkeypatch.setattr(rag_service, "ingest_document", AsyncMock(return_value=result))
        try:
            response = await client.post(
                DOCUMENTS,
                json=DOCUMENT_PAYLOAD,
                headers={**await csrf(), "X-Workspace-Id": str(WORKSPACE)},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 201
        assert "hybrid retrieval" in response.json()["note"]

    async def test_duplicate_is_201_and_says_nothing_was_written(
        self, client: httpx.AsyncClient, csrf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Idempotent on content hash, and the response admits it.

        A scheduled job refetching marketplace rules would otherwise stack
        copies of one document until a duplicate could outvote the sources that
        agree with it, so the caller has to know no write happened.
        """
        app.dependency_overrides.update(_override(UserRole.DATA_STEWARD.value))
        result = rag_service.IngestResult(
            document_id=uuid.uuid4(),
            chunks_written=0,
            token_total=0,
            indexed_semantically=False,
            duplicate=True,
        )
        monkeypatch.setattr(rag_service, "ingest_document", AsyncMock(return_value=result))
        try:
            response = await client.post(
                DOCUMENTS,
                json=DOCUMENT_PAYLOAD,
                headers={**await csrf(), "X-Workspace-Id": str(WORKSPACE)},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 201
        body = response.json()
        assert body["duplicate"] is True
        assert body["chunks_written"] == 0
        assert "Nothing was written" in body["note"]


class TestSearchNote:
    async def _search(
        self,
        client: httpx.AsyncClient,
        result: rag_service.SearchResult,
        monkeypatch: pytest.MonkeyPatch,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        app.dependency_overrides.update(_override(UserRole.DATA_STEWARD.value))
        monkeypatch.setattr(rag_service, "search", AsyncMock(return_value=result))
        try:
            return await client.get(
                SEARCH,
                params=params or {"q": "title limit"},
                headers={"X-Workspace-Id": str(WORKSPACE)},
            )
        finally:
            app.dependency_overrides.clear()

    async def test_keyword_only_results_are_labelled_as_such(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The caveat §37 needs: no embedding provider, no semantic claim."""
        result = rag_service.SearchResult(
            hits=[],
            signals=("keyword",),
            candidates_considered=4,
            query="title limit",
        )
        response = await self._search(client, result, monkeypatch)

        assert response.status_code == 200
        body = response.json()
        assert body["signals"] == ["keyword"]
        assert "No embedding provider is configured" in body["note"]
        assert "not a judgement that the rule is relevant" in body["note"]

    async def test_semantic_results_are_not_over_claimed(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With vectors in play, the caveat is dropped but signals still show."""
        chunk_id = uuid.uuid4()
        document_id = uuid.uuid4()
        hit = rag_service.RetrievalHit(
            chunk_id=chunk_id,
            document_id=document_id,
            content="Product titles must not exceed 200 characters.",
            section_reference="Section 12.3",
            document_title="Amazon Apparel Title Requirements",
            marketplace_code="amazon",
            score=0.87,
            signals=("semantic", "keyword"),
        )
        result = rag_service.SearchResult(
            hits=[hit],
            signals=("semantic", "keyword"),
            candidates_considered=12,
            query="title limit",
        )
        response = await self._search(client, result, monkeypatch)

        assert response.status_code == 200
        body = response.json()
        assert "semantic" in body["signals"]
        assert "No embedding provider" not in body["note"]
        assert "Evidence for a recommendation, not a compliance decision" in (
            body["note"]
        )

        # The citation is the whole point of §13: document, section, marketplace.
        first = body["hits"][0]
        assert first["document_id"] == str(document_id)
        assert first["section_reference"] == "Section 12.3"
        assert first["marketplace_code"] == "amazon"
        assert first["signals"] == ["semantic", "keyword"]

    async def test_metadata_filters_reach_the_service(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Filters must be pushed into SQL, not applied after retrieval.

        Applied afterwards, a hit from the wrong marketplace would already be
        inside the top-k and dropping it would return fewer results while the
        ranking had been shaped by documents the caller did not ask for.
        """
        captured: dict[str, Any] = {}

        async def fake_search(
            db: Any,
            tenant: TenantContext,
            *,
            query: str,
            metadata_filter: rag_service.MetadataFilter,
            top_k: int,
        ) -> rag_service.SearchResult:
            captured.update(
                query=query,
                metadata_filter=metadata_filter,
                top_k=top_k,
                workspace_id=tenant.workspace_id,
            )
            return rag_service.SearchResult(
                hits=[],
                signals=("metadata",),
                candidates_considered=0,
                query=query,
            )

        app.dependency_overrides.update(_override(UserRole.DATA_STEWARD.value))
        monkeypatch.setattr(rag_service, "search", fake_search)
        try:
            response = await client.get(
                SEARCH,
                params={
                    "q": "title limit",
                    "top_k": 5,
                    "marketplace_code": "amazon",
                    "country_code": "US",
                    "rule_kind": "listing",
                    "category_path": "Clothing > Shirts",
                },
                headers={"X-Workspace-Id": str(WORKSPACE)},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        assert captured["workspace_id"] == WORKSPACE, "tenant binding was dropped"
        assert captured["top_k"] == 5
        metadata = captured["metadata_filter"]
        assert metadata.marketplace_code == "amazon"
        assert metadata.country_code == "US"
        assert metadata.rule_kind == "listing"
        assert metadata.category_path == "Clothing > Shirts"
        assert metadata.document_kind is None
        assert metadata.effective_on is None
