"""Retrieval against a real pgvector (Phase 24, §40).

D-006 chose pgvector and D-021 chose hybrid retrieval, and until now neither had
been executed by a database. The unit suite asserts the *shape* of the SQL and
the arithmetic of the fusion; this file asserts that the SQL runs, that the
HNSW index that shipped without an operator class now builds, and that the rows
coming back are the ones that belong to the caller.

The embedding provider is deterministic and local. What is under test is the
database half of retrieval — index, distance operator, filters and isolation —
not a model. A live provider would make these tests slow, flaky and dependent on
a credential, and the D-007 layer already covers the provider contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import TenantContext
from app.rag.embeddings import EMBEDDING_DIMENSION
from app.rag.service import MetadataFilter, ingest_document, search

# Two topics far apart in vector space, so a nearest-neighbour assertion means
# something. A real embedding model would put these roughly here too.
TOPICS: dict[str, tuple[str, ...]] = {
    "labeling": ("label", "labeling", "textile", "fibre", "origin"),
    "safety": ("warning", "hazard", "battery", "charger", "safety"),
}


def _vector_for(text_value: str) -> list[float]:
    """A bag-of-topics vector: deterministic, and genuinely ordered by topic."""

    lowered = text_value.lower()
    vector = [0.0] * EMBEDDING_DIMENSION
    for index, (_topic, words) in enumerate(TOPICS.items()):
        hits = sum(1 for word in words if word in lowered)
        if hits:
            vector[index] = float(hits) / float(len(words))
    # A constant component so a vector of all zeros (a document matching no
    # known topic) is still a valid, finite vector rather than a NULL.
    vector[len(TOPICS)] = 1.0
    return vector


class _TopicEmbedder:
    """An `EmbeddingProvider` that needs no credential and no network."""

    name = "topic-hash"
    model = "topic-hash-v1"
    dimension = EMBEDDING_DIMENSION
    available = True

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [_vector_for(value) for value in texts]


@pytest.fixture
def embedder() -> _TopicEmbedder:
    return _TopicEmbedder()


async def _seed_corpus(
    session: AsyncSession, tenant: TenantContext, embedder: _TopicEmbedder
) -> None:
    """Two documents on different topics, both in the same marketplace."""

    await ingest_document(
        session,
        tenant,
        title="Textile labeling policy",
        content=(
            "Every textile product must carry a fiber content label listing the "
            "composition of each fibre and the country of origin of the textile."
        ),
        marketplace_code="AMZ",
        country_code="DE",
        document_kind="policy",
        rule_kind="labeling",
        effective_date=datetime(2026, 1, 1, tzinfo=UTC),
        embedder=embedder,
    )
    await ingest_document(
        session,
        tenant,
        title="Battery safety notice",
        content=(
            "Products with a lithium battery must display a safety warning about "
            "the charger and the hazard of a damaged battery cell."
        ),
        marketplace_code="AMZ",
        country_code="DE",
        document_kind="policy",
        rule_kind="safety",
        effective_date=datetime(2026, 1, 1, tzinfo=UTC),
        embedder=embedder,
    )
    await session.commit()


@pytest.mark.integration
class TestTheVectorIndexExists:
    async def test_the_hnsw_index_was_built_with_a_cosine_operator_class(
        self, db_session: AsyncSession
    ) -> None:
        """The bug this phase found: an HNSW index with no operator class.

        pgvector 0.8 rejects the DDL outright, so `migrate` failed on a real
        database and D-006's vector search could never have started. Asserted
        from `pg_indexes` because that is where the failure actually happened.
        """
        rows = (
            await db_session.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE tablename = 'compliance_chunks' "
                    "AND indexdef ILIKE '%hnsw%'"
                )
            )
        ).scalars().all()

        assert rows, "no HNSW index on compliance_chunks"
        definition = rows[0]
        assert "vector_cosine_ops" in definition
        assert "USING hnsw" in definition

    async def test_the_column_is_the_configured_width(
        self, db_session: AsyncSession
    ) -> None:
        """A 1536-dimension model and a 768-dimension column is silent loss."""

        width = (
            await db_session.execute(
                text(
                    "SELECT atttypmod FROM pg_attribute "
                    "WHERE attrelid = 'compliance_chunks'::regclass "
                    "AND attname = 'embedding'"
                )
            )
        ).scalar_one()

        assert width == EMBEDDING_DIMENSION


@pytest.mark.integration
class TestRetrievalAgainstRealVectors:
    async def test_a_query_returns_the_matching_document_first(
        self,
        db_session: AsyncSession,
        workspace: TenantContext,
        embedder: _TopicEmbedder,
    ) -> None:
        await _seed_corpus(db_session, workspace, embedder)

        result = await search(
            db_session,
            workspace,
            query="fiber composition label and country of origin",
            metadata_filter=MetadataFilter(),
            top_k=5,
            embedder=embedder,
        )

        assert result.hits, "hybrid retrieval returned nothing from a seeded corpus"
        assert "Textile labeling policy" in result.hits[0].document_title

    async def test_the_other_topic_is_not_returned_as_the_answer(
        self,
        db_session: AsyncSession,
        workspace: TenantContext,
        embedder: _TopicEmbedder,
    ) -> None:
        await _seed_corpus(db_session, workspace, embedder)

        result = await search(
            db_session,
            workspace,
            query="lithium battery hazard warning for the charger",
            metadata_filter=MetadataFilter(),
            top_k=5,
            embedder=embedder,
        )

        assert "Battery safety notice" in result.hits[0].document_title

    async def test_a_filter_excludes_before_retrieval(
        self,
        db_session: AsyncSession,
        workspace: TenantContext,
        embedder: _TopicEmbedder,
    ) -> None:
        """D-021: filters belong in SQL, not in a post-retrieval `if`."""

        await _seed_corpus(db_session, workspace, embedder)

        result = await search(
            db_session,
            workspace,
            query="fiber composition label",
            metadata_filter=MetadataFilter(marketplace_code="ETSY"),
            top_k=5,
            embedder=embedder,
        )

        assert result.hits == []

    async def test_a_not_yet_effective_rule_is_excluded(
        self, db_session: AsyncSession,
        workspace: TenantContext,
        embedder: _TopicEmbedder,
    ) -> None:
        await _seed_corpus(db_session, workspace, embedder)
        yesterday = datetime.now(UTC).replace(year=2020, month=1, day=1)

        result = await search(
            db_session,
            workspace,
            query="fiber composition label",
            metadata_filter=MetadataFilter(effective_on=yesterday),
            top_k=5,
            embedder=embedder,
        )

        assert result.hits == []

    async def test_an_every_candidate_is_cited(
        self,
        db_session: AsyncSession,
        workspace: TenantContext,
        embedder: _TopicEmbedder,
    ) -> None:
        """§37: a claim without a retrievable source is not evidence."""

        await _seed_corpus(db_session, workspace, embedder)

        result = await search(
            db_session,
            workspace,
            query="label",
            metadata_filter=MetadataFilter(),
            top_k=5,
            embedder=embedder,
        )

        assert result.hits
        for hit in result.hits:
            citation = hit.citation
            assert citation["document_id"] == str(hit.document_id)
            assert citation["document_title"] == hit.document_title
            # Every hit carries enough to find the rule again: §13's promise is
            # that a reviewer can point at the exact text.
            assert hit.content


@pytest.mark.integration
class TestRetrievalRespectsTheTenant:
    async def test_one_workspace_cannot_retrieve_another_workspaces_corpus(
        self,
        db_session: AsyncSession,
        workspace: TenantContext,
        other_workspace: TenantContext,
        embedder: _TopicEmbedder,
    ) -> None:
        await _seed_corpus(db_session, workspace, embedder)

        result = await search(
            db_session,
            other_workspace,
            query="fiber composition label and country of origin",
            metadata_filter=MetadataFilter(),
            top_k=5,
            embedder=embedder,
        )

        assert result.hits == [], "retrieval crossed the tenant boundary"