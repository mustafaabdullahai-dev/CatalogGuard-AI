"""Compliance knowledge: ingest and retrieve (§13, §14).

Two halves with deliberately different trust postures.

**Ingest** is deterministic: hash, chunk, optionally embed, store. Idempotent by
`uq_doc_content_hash`, so re-posting the same policy document updates nothing and
duplicates nothing — a script that refetches rules nightly will not build a corpus
of identical copies.

**Retrieve** is hybrid and reports its evidence: a tsvector pass for exact terms
and identifiers, a vector pass for meaning when a provider exists, then the
deterministic reranker in `retrieval.py`. Neither half calls a model to *decide*
anything; §13 asks the AI to cite the exact rule, and a citation that cannot be
re-derived is not a citation.

Verification status: everything here runs without a database or a model, so the
logic is unit tested. What is **unverified** is the live path — chunk rows have
never been written to Neon, `to_tsvector` has never run there, and the vector pass
has never executed (no key, and pgvector is unconfirmed on D-006).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.telemetry import emit
from app.db.base import TenantContext
from app.models.platform import ComplianceChunk, ComplianceDocument
from app.rag.chunking import chunk_text
from app.rag.embeddings import (
    EmbeddingProvider,
    embed_if_available,
    get_embedder,
)
from app.rag.repositories import (
    ComplianceChunkRepository,
    ComplianceDocumentRepository,
)
from app.rag.retrieval import Candidate, RetrievalHit, rerank, signals_used

logger = get_logger(__name__)

# `search_vector` is a plain column, not a generated one (the ORM declares
# TSVECTOR with no computed expression), so it is filled here. Without it the GIN
# index would sit on a column that is always NULL.
_INDEX_SQL = sql_text(
    "UPDATE compliance_chunks SET search_vector = to_tsvector('simple', content) "
    "WHERE workspace_id = :workspace_id AND document_id = :document_id"
)


def content_hash(content: str) -> str:
    """sha256 of the document text — the dedup key `uq_doc_content_hash` enforces."""
    return sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class IngestResult:
    document_id: UUID
    chunks_written: int
    token_total: int
    indexed_semantically: bool
    duplicate: bool


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: list[RetrievalHit]
    signals: tuple[str, ...]
    candidates_considered: int
    query: str


@dataclass(frozen=True, slots=True)
class MetadataFilter:
    """The §14 metadata predicates, as SQL-level indexed filters.

    These are columns rather than chunk payload keys precisely so they can be
    pushed into the query. Filtering after retrieval would mean a semantic hit
    from the wrong marketplace or the wrong version had already been selected —
    the filter would then silently shrink an already-wrong candidate set instead
    of preventing it.
    """

    marketplace_code: str | None = None
    country_code: str | None = None
    document_kind: str | None = None
    rule_kind: str | None = None
    version_label: str | None = None
    category_path: str | None = None
    effective_on: datetime | None = None

    @property
    def is_empty(self) -> bool:
        return all(value is None for value in self.as_values().values())

    def as_values(self) -> dict[str, str | datetime | None]:
        return {
            "marketplace_code": self.marketplace_code,
            "country_code": self.country_code,
            "document_kind": self.document_kind,
            "rule_kind": self.rule_kind,
            "version_label": self.version_label,
            "category_path": self.category_path,
            "effective_on": self.effective_on,
        }

    def apply(
        self, document: type[ComplianceDocument]
    ) -> ColumnElement[bool] | None:
        """AND-ed predicates on the document table. `None` means "any".

        Written out per field rather than looping over `as_values()`, because
        the date and category predicates are not plain equality and a generic
        loop would quietly apply the wrong operator to them.
        """
        conditions: list[ColumnElement[bool]] = []

        # Equality predicates. Each is an indexed column (D-006 phase 13), so
        # these narrow the scan rather than filtering its output.
        if self.marketplace_code is not None:
            conditions.append(document.marketplace_code == self.marketplace_code)
        if self.country_code is not None:
            conditions.append(document.country_code == self.country_code)
        if self.document_kind is not None:
            conditions.append(document.document_kind == self.document_kind)
        if self.rule_kind is not None:
            conditions.append(document.rule_kind == self.rule_kind)
        if self.version_label is not None:
            conditions.append(document.version_label == self.version_label)

        if self.category_path is not None:
            # `category_path_text` holds a path such as
            # "Clothing > Shirts > Men's Shirts". Matching anywhere in the path
            # means a rule filed under "Shirts" still covers "Men's Shirts",
            # which is what a reviewer filtering by category expects.
            conditions.append(
                document.category_path_text.ilike(f"%{self.category_path}%")
            )

        if self.effective_on is not None:
            # A rule with no effective date is NOT excluded by a date filter:
            # undated policy is still policy, and silently dropping it would
            # hide a rule the reviewer needs to see. A rule whose effective date
            # is *later* than the date asked about is excluded -- it was not in
            # force yet, so citing it as current would be wrong.
            conditions.append(
                or_(
                    document.effective_date.is_(None),
                    document.effective_date <= self.effective_on,
                )
            )

        if not conditions:
            return None
        return and_(*conditions)


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


async def ingest_document(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    title: str,
    content: str,
    source_url: str | None = None,
    marketplace_code: str | None = None,
    country_code: str | None = None,
    category_path: str | None = None,
    document_kind: str | None = None,
    rule_kind: str | None = None,
    version_label: str | None = None,
    effective_date: datetime | None = None,
    embedder: EmbeddingProvider | None = None,
) -> IngestResult:
    """Store one policy document and its retrievable chunks.

    Idempotent on `content_hash` (enforced by `uq_doc_content_hash`): re-posting
    an unchanged document returns the existing row and writes nothing. A nightly
    script that refetches every rule page therefore cannot build a corpus of
    identical copies, which would otherwise let one duplicated rule out-vote
    three others that agree with it during retrieval.

    Metadata is recorded on the document, not only on each chunk, so a search can
    filter by marketplace and effective date in SQL instead of after retrieval.
    """
    if not title or not title.strip():
        raise ValueError("title is required")
    if not content or not content.strip():
        raise ValueError("content is required")

    settings = get_settings()
    digest = content_hash(content)
    docs = ComplianceDocumentRepository(session, tenant)

    existing = (
        await session.execute(
            select(ComplianceDocument).where(
                ComplianceDocument.workspace_id == tenant.workspace_id,
                ComplianceDocument.content_hash == digest,
                ComplianceDocument.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        logger.info(
            "compliance_document_duplicate",
            document_id=str(existing.id),
            title=title,
        )
        return IngestResult(
            document_id=existing.id,
            chunks_written=0,
            token_total=0,
            indexed_semantically=False,
            duplicate=True,
        )

    document = await docs.add(
        ComplianceDocument(
            title=title.strip(),
            content_hash=digest,
            source_url=source_url,
            marketplace_code=marketplace_code,
            country_code=country_code,
            category_path_text=category_path,
            document_kind=document_kind,
            rule_kind=rule_kind,
            version_label=version_label,
            effective_date=effective_date,
        )
    )

    chunks = chunk_text(
        content,
        max_chars=settings.rag_chunk_max_chars,
        overlap=settings.rag_chunk_overlap,
    )
    if not chunks:
        raise ValueError("content produced no chunks")

    # Embedding happens before the rows are added so a provider failure raises
    # rather than storing chunks that look indexed and never will be.
    provider = embedder if embedder is not None else get_embedder()
    vectors = await embed_if_available(provider, [chunk.content for chunk in chunks])

    chunk_repo = ComplianceChunkRepository(session, tenant)
    for chunk in chunks:
        await chunk_repo.add(
            ComplianceChunk(
                document_id=document.id,
                chunk_index=chunk.index,
                content=chunk.content,
                section_reference=chunk.section_reference,
                token_count=chunk.token_count,
                embedding=vectors[chunk.index] if vectors is not None else None,
            )
        )

    # `search_vector` is a plain column, not a generated one (see _INDEX_SQL),
    # so the GIN index would otherwise sit on a column that is always NULL.
    await session.execute(_INDEX_SQL, {"workspace_id": tenant.workspace_id,
                                      "document_id": document.id})

    logger.info(
        "compliance_document_ingested",
        document_id=str(document.id),
        chunks=len(chunks),
        indexed_semantically=vectors is not None,
        provider=provider.name,
    )
    return IngestResult(
        document_id=document.id,
        chunks_written=len(chunks),
        token_total=sum(chunk.token_count for chunk in chunks),
        indexed_semantically=vectors is not None,
        duplicate=False,
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _tsquery(query: str) -> object:
    """`websearch_to_tsquery` so a user query cannot inject tsquery syntax.

    `to_tsquery` takes a language expression where `&`, `|`, `!` and `'` are
    operators; a user typing `!` would be changing the query rather than
    searching for it. `websearch_to_tsquery` reads the whole string as text and
    quotes what it should not treat as syntax.
    """
    return sql_text("websearch_to_tsquery('simple', :q)").bindparams(q=query)


def keyword_statement(
    *,
    workspace_id: UUID,
    query: str,
    metadata_filter: MetadataFilter,
    limit: int,
) -> Select[Any]:
    """Build the exact-term retrieval statement.

    Split from execution so the WHERE clause can be asserted offline. The tenant
    predicate is the thing worth proving here: D-011 makes isolation a property
    of every query, and a retrieval path that quietly omitted it would leak one
    workspace's marketplace policy into another's compliance answer — which is
    also how one tenant's contract terms would surface in another's review.
    """
    document_filter = metadata_filter.apply(ComplianceDocument)
    statement = (
        select(
            ComplianceChunk.id,
            ComplianceChunk.document_id,
            ComplianceChunk.content,
            ComplianceChunk.section_reference,
            ComplianceDocument.title,
            ComplianceDocument.marketplace_code,
        )
        .join(
            ComplianceDocument,
            (ComplianceChunk.document_id == ComplianceDocument.id)
            & (ComplianceChunk.workspace_id == ComplianceDocument.workspace_id),
        )
        .where(ComplianceChunk.workspace_id == workspace_id)
        .where(ComplianceChunk.deleted_at.is_(None))
        .where(ComplianceDocument.deleted_at.is_(None))
        .where(ComplianceChunk.search_vector.op("@@")(_tsquery(query)))
    )
    if document_filter is not None:
        statement = statement.where(document_filter)
    return cast(
        "Select[Any]",
        statement.order_by(
            func.ts_rank(ComplianceChunk.search_vector, _tsquery(query)).desc()
        ).limit(limit),
    )


async def _keyword_pass(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    query: str,
    metadata_filter: MetadataFilter,
    limit: int,
) -> list[tuple[Candidate, int]]:
    """The exact-term half of hybrid retrieval, over the GIN tsvector index.

    Ranked by `ts_rank` so the pass also orders its own candidates; the
    deterministic reranker then blends this order with any semantic score.
    """
    statement = keyword_statement(
        workspace_id=tenant.workspace_id,
        query=query,
        metadata_filter=metadata_filter,
        limit=limit,
    )
    rows = await session.execute(statement)
    candidates: list[tuple[Candidate, int]] = []
    for rank, row in enumerate(rows.all(), start=1):
        candidates.append(
            (
                Candidate(
                    chunk_id=row.id,
                    document_id=row.document_id,
                    content=row.content,
                    section_reference=row.section_reference,
                    document_title=row.title,
                    marketplace_code=row.marketplace_code,
                    keyword_rank=rank,
                ),
                rank,
            )
        )
    return candidates


def vector_statement(
    *,
    workspace_id: UUID,
    query_vector: Sequence[float],
    metadata_filter: MetadataFilter,
    limit: int,
) -> Select[Any]:
    """Build the semantic retrieval statement. Same reasoning as above.

    `embedding IS NOT NULL` is kept in the WHERE clause rather than left to the
    index: a corpus stored unindexed has a NULL vector everywhere, and the pass
    must return nothing rather than every row at distance zero.
    """
    document_filter = metadata_filter.apply(ComplianceDocument)
    distance = ComplianceChunk.embedding.cosine_distance(list(query_vector))
    statement = (
        select(
            ComplianceChunk.id,
            ComplianceChunk.document_id,
            ComplianceChunk.content,
            ComplianceChunk.section_reference,
            ComplianceDocument.title,
            ComplianceDocument.marketplace_code,
            distance.label("distance"),
        )
        .join(
            ComplianceDocument,
            (ComplianceChunk.document_id == ComplianceDocument.id)
            & (ComplianceChunk.workspace_id == ComplianceDocument.workspace_id),
        )
        .where(ComplianceChunk.workspace_id == workspace_id)
        .where(ComplianceChunk.deleted_at.is_(None))
        .where(ComplianceDocument.deleted_at.is_(None))
        .where(ComplianceChunk.embedding.isnot(None))
    )
    if document_filter is not None:
        statement = statement.where(document_filter)
    return cast("Select[Any]", statement.order_by(distance).limit(limit))


async def _vector_pass(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    query_vector: Sequence[float],
    metadata_filter: MetadataFilter,
    limit: int,
) -> list[Candidate]:
    """The semantic half, over the HNSW index. Cosine distance, nearest first."""
    statement = vector_statement(
        workspace_id=tenant.workspace_id,
        query_vector=query_vector,
        metadata_filter=metadata_filter,
        limit=limit,
    )
    rows = await session.execute(statement)
    return [
        Candidate(
            chunk_id=row.id,
            document_id=row.document_id,
            content=row.content,
            section_reference=row.section_reference,
            document_title=row.title,
            marketplace_code=row.marketplace_code,
            # Cosine distance is 0..2; map to a 0..1 similarity so the weights in
            # `combine()` mean the same thing regardless of which provider ran.
            similarity=max(0.0, 1.0 - float(row.distance)),
        )
        for row in rows.all()
    ]


async def search(
    session: AsyncSession,
    tenant: TenantContext,
    *,
    query: str,
    metadata_filter: MetadataFilter | None = None,
    top_k: int | None = None,
    candidate_limit: int | None = None,
    embedder: EmbeddingProvider | None = None,
) -> SearchResult:
    """Hybrid compliance search returning citable evidence (§13, §14).

    Both passes run against the same metadata-filtered set and their candidates
    are merged by chunk id, so a chunk found by both signals keeps its semantic
    similarity *and* its keyword rank instead of appearing twice.

    `signals` states what this deployment could actually search with. A
    workspace running without embeddings gets `keyword, metadata` and no claim of
    semantic understanding.
    """
    if not query or not query.strip():
        raise ValueError("query is required")

    settings = get_settings()
    metadata_filter = metadata_filter or MetadataFilter()
    top_k = top_k or settings.rag_retrieval_results
    candidate_limit = candidate_limit or settings.rag_retrieval_candidates

    provider = embedder if embedder is not None else get_embedder()

    retrieval_started = time.perf_counter()
    merged: dict[UUID, Candidate] = {}
    for candidate, rank in await _keyword_pass(
        session, tenant, query=query,
        metadata_filter=metadata_filter, limit=candidate_limit,
    ):
        merged[candidate.chunk_id] = replace(candidate, keyword_rank=rank)

    vectors: list[list[float]] | None = None
    if provider.available:
        vectors = await embed_if_available(provider, [query])

    if vectors is not None:
        for candidate in await _vector_pass(
            session, tenant, query_vector=vectors[0],
            metadata_filter=metadata_filter, limit=candidate_limit,
        ):
            existing = merged.get(candidate.chunk_id)
            if existing is None:
                merged[candidate.chunk_id] = candidate
            else:
                # Both passes found it. The candidate carries both facts; the
                # reranker is the only thing that turns them into a score.
                merged[candidate.chunk_id] = replace(
                    existing,
                    similarity=candidate.similarity,
                    section_reference=existing.section_reference
                    or candidate.section_reference,
                )

    hits = rerank(query, list(merged.values()), top_k=top_k)
    signals = signals_used(embedder_available=vectors is not None)
    logger.info(
        "compliance_search",
        candidates=len(merged),
        returned=len(hits),
        provider=provider.name,
        filter_active=not metadata_filter.is_empty,
    )
    # §32's "retrieval quality". Counts, provider and signals — never the query:
    # a compliance question is written from catalog content, and D-010 keeps that
    # content out of telemetry. An empty result set is worth seeing as much as a
    # full one, so it is emitted with the same shape and `returned=0`.
    emit(
        "retrieval",
        "compliance_search",
        duration_ms=int((time.perf_counter() - retrieval_started) * 1000),
        status="ok" if hits else "empty",
        workspace_id=str(tenant.workspace_id),
        attributes={
            "candidates": len(merged),
            "returned": len(hits),
            "top_k": top_k,
            "provider": provider.name,
            "signals": list(signals),
            "filter_active": not metadata_filter.is_empty,
        },
    )
    return SearchResult(
        hits=hits,
        signals=signals,
        candidates_considered=len(merged),
        query=query,
    )
