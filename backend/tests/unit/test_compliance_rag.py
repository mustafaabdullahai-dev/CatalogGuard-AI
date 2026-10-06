"""Chunking, hybrid retrieval, and compliance-corpus logic (Phase 13, §13, §14).

Everything here runs with no database and no model. The SQL passes in
`app.rag.service` are compiled and asserted against — not executed — because
`to_tsvector` and `cosine_distance` need a live Postgres with pgvector, and D-006
records that pgvector is still unconfirmed on Neon. Asserting on compiled SQL
catches the failure this phase can actually catch: a filter that silently does
not appear in the WHERE clause, or a query that forgets to be tenant-scoped.

What is NOT verified here, and is not claimed anywhere: that a chunk row has ever
been written to Neon, that `to_tsvector` has ever run, or that the vector pass
has ever executed. See the module docstring in `app.rag.service`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.rag import embeddings
from app.rag.chunking import chunk_text, estimate_tokens, keywords, split_sections
from app.rag.embeddings import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DIMENSION,
    EmbeddingContractError,
    EmbeddingUnavailableError,
    NullEmbedder,
    embed_if_available,
    get_embedder,
)
from app.rag.retrieval import (
    Candidate,
    combine,
    lexical_score,
    rerank,
    signals_used,
)
from app.rag.service import (
    MetadataFilter,
    content_hash,
    keyword_statement,
    vector_statement,
)

POLICY_TEXT = """# Amazon Apparel Listing Requirements

## Section 3.1 Title

Product titles must not exceed 200 characters, including spaces. Titles must not
contain promotional phrases such as "best seller" or "free shipping".

## Section 4.2 Images

The main image must have a pure white background. Minimum resolution is 1000
pixels on the longest side.

## Section 5.0 Prohibited claims

Listings must not make health or therapeutic claims about a garment.
"""

DOC_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")


def _candidate(
    content: str,
    *,
    similarity: float | None = None,
    keyword_rank: int | None = None,
    section: str | None = "Section 3.1",
    marketplace: str | None = "amazon",
) -> Candidate:
    return Candidate(
        chunk_id=uuid.uuid4(),
        document_id=DOC_ID,
        content=content,
        section_reference=section,
        document_title="Amazon Apparel Listing Requirements",
        marketplace_code=marketplace,
        similarity=similarity,
        keyword_rank=keyword_rank,
    )


class TestChunking:
    def test_headings_become_section_references(self) -> None:
        """§13 asks a recommendation to cite the rule. That needs the heading."""
        chunks = chunk_text(POLICY_TEXT)
        references = {chunk.section_reference for chunk in chunks}
        assert "Section 3.1 Title" in references
        assert "Section 4.2 Images" in references
        assert "Section 5.0 Prohibited claims" in references

    def test_every_chunk_carries_the_reference_of_its_own_section(self) -> None:
        chunks = chunk_text(POLICY_TEXT)
        by_reference = {chunk.section_reference: chunk for chunk in chunks}
        image_chunk = by_reference["Section 4.2 Images"]
        assert "pure white" in image_chunk.content
        assert "200 characters" not in image_chunk.content

    def test_chunk_indices_are_dense_and_ordered(self) -> None:
        chunks = chunk_text(POLICY_TEXT)
        assert [chunk.index for chunk in chunks] == list(range(len(chunks)))

    def test_a_section_title_is_inside_the_searchable_text(self) -> None:
        """`section_reference` labels a citation; only `content` is indexed.

        A query asking about "prohibited claims" has to match on the heading,
        because the heading is not stored anywhere else the search reads.
        """
        chunks = chunk_text(POLICY_TEXT)
        assert any("## Section 5.0 Prohibited claims" in chunk.content for chunk in chunks)

    def test_empty_text_produces_no_chunks(self) -> None:
        assert chunk_text("") == []
        assert chunk_text("   \n\n  ") == []

    def test_max_chars_is_respected(self) -> None:
        long_section = "\n\n".join(f"Paragraph {i}. " * 20 for i in range(20))
        chunks = chunk_text(long_section, max_chars=200, overlap=0)
        assert chunks
        assert all(len(chunk.content) <= 200 for chunk in chunks)

    def test_carried_overlap_never_exceeds_max_chars(self) -> None:
        """The overlap is context, not a licence to overflow the chunk.

        A unit that just barely does not fit used to be re-issued with the
        whole carried tail prepended, which could set a chunk well past
        `max_chars` — retrieval truncates that silently, on precisely the rule
        the overlap was there to protect.
        """
        chunks = chunk_text("a" * 190 + "\n\n" + "b" * 190, max_chars=200, overlap=100)
        assert chunks
        assert all(len(chunk.content) <= 200 for chunk in chunks)
        assert chunks[1].content != "b" * 190  # the boundary was still carried

    def test_oversized_paragraph_boundaries_carry_overlap(self) -> None:
        """A paragraph split mid-sentence needs context as much as one that fits."""
        sentence = "Rule text that must be preserved. " * 40
        chunks = chunk_text(sentence, max_chars=200, overlap=50)
        assert len(chunks) > 1
        assert all(len(chunk.content) <= 200 for chunk in chunks)
        assert chunks[1].content != sentence[:200]
        # One long paragraph used to become one chunk *per sentence*: dozens of
        # 33-character chunks that rank on their own and hold no rule. The
        # pieces are repacked, so a chunk is still worth retrieving.
        assert all(len(chunk.content) >= 150 for chunk in chunks)

    def test_overlap_cannot_exceed_half_the_chunk(self) -> None:
        """An overlap larger than the chunk would duplicate the whole chunk."""
        chunks = chunk_text(POLICY_TEXT, max_chars=300, overlap=10_000)
        assert all(len(chunk.content) <= 300 for chunk in chunks)

    def test_rejects_a_non_positive_max_chars(self) -> None:
        with pytest.raises(ValueError, match="max_chars"):
            chunk_text(POLICY_TEXT, max_chars=0)

    def test_a_heading_with_no_body_is_dropped(self) -> None:
        """A bare heading is a table of contents line, not a rule to cite."""
        chunks = chunk_text("# Title\n\n## Section 1\n\n## Section 2\n\nReal text.")
        # The two bare headings produce no chunk: their entire content would be
        # a title, which matches on every other title too. The heading that has
        # a body travels *inside* its chunk, because `search_vector` reads only
        # `content` and a section title is the phrase most likely to be searched.
        assert [chunk.content for chunk in chunks] == ["## Section 2\n\nReal text."]

    def test_split_sections_keeps_preamble_content(self) -> None:
        sections = split_sections("Intro text.\n\n# Heading\n\nBody text.")
        assert sections[0][0] is None
        assert sections[0][1].strip() == "Intro text."
        # The heading line stays in the body: dropping it removed a line from
        # the indexed text and left the section title unsearchable. The blank
        # line is kept here; `chunk_text` strips it.
        assert sections[1][0] == "Heading"
        assert sections[1][1].strip() == "# Heading\n\nBody text."

    def test_section_style_without_markdown_is_recognised(self) -> None:
        """Marketplace help pages title rules "Section 12.3" without hashes."""
        chunks = chunk_text("Section 12.3 Product detail page\n\nBullets here.")
        assert chunks[0].section_reference is not None
        assert "12.3" in chunks[0].section_reference

    def test_token_estimate_tracks_length(self) -> None:
        assert estimate_tokens("a" * 400) == 100
        assert estimate_tokens("a") == 1

    def test_keywords_keeps_rule_bearing_words(self) -> None:
        """Stripping "must"/"not" would change what a rule means (§14)."""
        terms = keywords("Titles must not exceed 200 characters")
        assert "must" in terms
        assert "not" in terms

    def test_keywords_are_lowercase_distinct_and_bounded(self) -> None:
        terms = keywords("Title TITLE title Body", limit=10)
        assert terms == [term.casefold() for term in terms]
        assert len(set(terms)) == len(terms)
        assert len(keywords("a b c d e f", limit=3)) == 3


class TestLexicalScoring:
    def test_overlap_is_a_fraction_of_the_query_terms(self) -> None:
        query_terms = keywords("title character limit")
        score = lexical_score(
            "A limit on title character count",
            query_terms,
            "A limit on title character count",
        )
        assert 0.0 < score <= 1.0

    def test_no_shared_terms_scores_zero(self) -> None:
        assert lexical_score("colour", keywords("colour"), "material cotton") == 0.0

    def test_empty_query_terms_score_zero(self) -> None:
        assert lexical_score("anything", [], "anything at all") == 0.0

    def test_verbatim_phrase_outscores_scattered_terms(self) -> None:
        query = "must not exceed 200 characters"
        terms = keywords(query)
        exact = lexical_score(query, terms, f"Preamble. {query}. Trailing text.")
        scattered = lexical_score(
            query,
            terms,
            "Titles must be short. 200 is a number. Characters count. "
            "Exceeding is not allowed in general.",
        )
        assert exact > scattered


class TestSignalCombination:
    def test_no_signals_scores_zero(self) -> None:
        assert combine(semantic=None, lexical=0.0) == (0.0, ())

    def test_keyword_only_names_only_keyword(self) -> None:
        score, signals = combine(semantic=None, lexical=0.8)
        assert signals == ("keyword",)
        assert score == pytest.approx(0.8)

    def test_weights_are_normalized_over_signals_that_fired(self) -> None:
        """Otherwise a pure-semantic hit loses to a mediocre keyword hit."""
        semantic_only, _ = combine(semantic=0.6, lexical=0.0)
        assert semantic_only == pytest.approx(0.6)
        both, signals = combine(semantic=1.0, lexical=1.0)
        assert signals == ("semantic", "keyword")
        assert both == pytest.approx(1.0)

    def test_deployment_without_embeddings_advertises_only_keyword(self) -> None:
        """A response must not look identical to a semantically-indexed one."""
        assert signals_used(embedder_available=False) == ("keyword", "metadata")
        assert "semantic" in signals_used(embedder_available=True)


class TestRerank:
    def test_candidates_with_no_evidence_are_dropped(self) -> None:
        hits = rerank(
            "title character limit",
            [_candidate("material 100% cotton", similarity=None)],
            top_k=5,
        )
        assert hits == []

    def test_semantic_only_hit_is_returned_with_semantic_signal(self) -> None:
        hits = rerank(
            "apparel requirements",
            [_candidate("unrelated words entirely", similarity=0.95)],
            top_k=5,
        )
        assert len(hits) == 1
        assert hits[0].signals == ("semantic",)

    def test_ordering_is_by_score_then_keyword_rank(self) -> None:
        hits = rerank(
            "title character limit",
            [
                _candidate("title character limit applies", similarity=None,
                           keyword_rank=2),
                _candidate("title character limit applies exactly",
                           similarity=None, keyword_rank=1),
            ],
            top_k=5,
        )
        # Equal scores: the keyword pass's own order settles it.
        assert [hit.content.endswith("exactly") for hit in hits] == [True, False]

    def test_top_k_caps_the_result(self) -> None:
        candidates = [
            _candidate(f"title character limit variant {i}", keyword_rank=i)
            for i in range(1, 11)
        ]
        assert len(rerank("title character limit", candidates, top_k=3)) == 3

    def test_ranking_is_reproducible(self) -> None:
        """§14: same query and corpus must return the same order tomorrow."""
        candidates = [
            _candidate("title character limit", keyword_rank=3),
            _candidate("title character limit", keyword_rank=1),
            _candidate("image background requirements", keyword_rank=2),
        ]
        first = [hit.chunk_id for hit in rerank("title character limit", candidates,
                                               top_k=5)]
        second = [hit.chunk_id for hit in rerank("title character limit", candidates,
                                                top_k=5)]
        assert first == second

    def test_hit_exposes_a_citation(self) -> None:
        hits = rerank(
            "title character limit",
            [_candidate("title character limit", keyword_rank=1)],
            top_k=1,
        )
        citation = hits[0].citation
        assert citation["document_id"] == str(DOC_ID)
        assert citation["section_reference"] == "Section 3.1"
        assert citation["marketplace"] == "amazon"


class TestEmbeddingPort:
    async def test_null_embedder_is_unavailable(self) -> None:
        provider = NullEmbedder()
        assert provider.available is False
        assert provider.name == "none"

    async def test_null_embedder_raises_rather_than_returning_empty(self) -> None:
        """A caller that forgets the check must fail, not store fake rows."""
        with pytest.raises(EmbeddingUnavailableError):
            await NullEmbedder().embed(["anything"])

    async def test_embed_if_available_returns_none_when_unavailable(self) -> None:
        assert await embed_if_available(NullEmbedder(), ["a", "b"]) is None

    async def test_embed_if_available_delegates_when_available(self) -> None:
        class Stub:
            name, model, dimension, available = "stub", "stub", 8, True

            async def embed(self, texts):
                return [[0.1] * 8 for _ in texts]

        assert await embed_if_available(Stub(), ["a"]) == [[0.1] * 8]

    async def test_a_short_vector_batch_is_refused(self) -> None:
        """One missing vector shifts every later vector onto the wrong chunk.

        The rows would be written, look indexed, and rank text by a
        neighbouring rule's embedding — with nothing in the response to say so.
        """
        class Stub:
            name, model, dimension, available = "stub", "stub", 8, True

            async def embed(self, texts):
                return [[0.1] * 8 for _ in texts][:-1]

        with pytest.raises(EmbeddingContractError, match="exactly one"):
            await embed_if_available(Stub(), ["a", "b", "c"])

    async def test_a_wrong_width_vector_is_refused(self) -> None:
        """A model that stopped returning 1536 dims must not reach the column."""
        class Stub:
            name, model, dimension, available = "stub", "stub", 8, True

            async def embed(self, texts):
                return [[0.1] * 4 for _ in texts]

        with pytest.raises(EmbeddingContractError, match="4-dimension"):
            await embed_if_available(Stub(), ["a"])

    async def test_embedding_requests_are_batched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A whole policy document in one request fails on the input limit."""
        sizes: list[int] = []

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "data": [
                        {"index": i, "embedding": [0.0] * EMBEDDING_DIMENSION}
                        for i in range(sizes[-1])
                    ]
                }

        class FakeClient:
            def __init__(self, *args: object, **kwargs: object) -> None:
                del args, kwargs

            async def __aenter__(self) -> FakeClient:
                return self

            async def __aexit__(self, *args: object) -> None:
                del args

            async def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
                del url, headers
                sizes.append(len(json["input"]))
                return FakeResponse()

        monkeypatch.setattr(embeddings.httpx, "AsyncClient", FakeClient)
        provider = embeddings.QroqEmbeddingProvider(
            api_key="key", base_url="https://example.test", model="embed", timeout=5
        )
        texts = [f"chunk {i}" for i in range(EMBEDDING_BATCH_SIZE + 1)]

        vectors = await embed_if_available(provider, texts)

        assert sizes == [EMBEDDING_BATCH_SIZE, 1]
        assert len(vectors or []) == len(texts)

    def test_default_mode_is_the_honest_one(self) -> None:
        """No provider configured must mean no vectors, not fake ones (§67)."""
        assert get_embedder().available is False

    def test_dimension_matches_the_column(self) -> None:
        """`compliance_chunks.embedding` is Vector(1536); a mismatch truncates."""
        assert EMBEDDING_DIMENSION == 1536


class TestContentHash:
    def test_same_text_hashes_identically(self) -> None:
        assert content_hash(POLICY_TEXT) == content_hash(POLICY_TEXT)

    def test_any_byte_change_changes_the_hash(self) -> None:
        assert content_hash("a limit of 200") != content_hash("a limit of 201")

    def test_hash_is_hex_sha256(self) -> None:
        digest = content_hash("policy")
        assert len(digest) == 64
        assert all(char in "0123456789abcdef" for char in digest)


def _sql(statement: object) -> str:
    """Compile a statement to Postgres SQL for a WHERE-clause assertion.

    The Postgres dialect is explicit: the generic dialect renders `ilike` as
    `lower(...) LIKE lower(...)`, so a check for "LIKE" would pass while never
    proving the production form is used.
    """
    from sqlalchemy.dialects import postgresql

    compiled = statement.compile(  # type: ignore[attr-defined]
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    return str(compiled)


WORKSPACE = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
OTHER_WORKSPACE = uuid.UUID("ffffffff-0000-1111-2222-333333333333")


class TestRetrievalStatements:
    """Both passes must be tenant-scoped and metadata-filtered (D-011, §14)."""

    def test_keyword_pass_is_scoped_to_the_bound_workspace(self) -> None:
        statement = keyword_statement(
            workspace_id=WORKSPACE,
            query="title limit",
            metadata_filter=MetadataFilter(),
            limit=10,
        )
        sql = _sql(statement)
        assert "workspace_id" in sql
        assert str(WORKSPACE) in sql
        assert str(OTHER_WORKSPACE) not in sql

    def test_keyword_pass_excludes_soft_deleted_rows(self) -> None:
        statement = keyword_statement(
            workspace_id=WORKSPACE,
            query="title limit",
            metadata_filter=MetadataFilter(),
            limit=10,
        )
        assert "deleted_at IS NULL" in _sql(statement)

    def test_keyword_pass_uses_the_full_text_match_operator(self) -> None:
        """`@@` against websearch_to_tsquery is what makes the GIN index usable."""
        statement = keyword_statement(
            workspace_id=WORKSPACE,
            query="title limit",
            metadata_filter=MetadataFilter(),
            limit=10,
        )
        sql = _sql(statement)
        assert "websearch_to_tsquery" in sql
        assert " @@ " in sql

    def test_keyword_pass_applies_metadata_filters(self) -> None:
        statement = keyword_statement(
            workspace_id=WORKSPACE,
            query="title limit",
            metadata_filter=MetadataFilter(marketplace_code="amazon"),
            limit=10,
        )
        sql = _sql(statement)
        assert "marketplace_code" in sql
        assert "'amazon'" in sql

    def test_keyword_pass_respects_the_candidate_limit(self) -> None:
        statement = keyword_statement(
            workspace_id=WORKSPACE,
            query="title limit",
            metadata_filter=MetadataFilter(),
            limit=7,
        )
        assert "LIMIT 7" in _sql(statement)

    def test_vector_pass_is_scoped_to_the_bound_workspace(self) -> None:
        statement = vector_statement(
            workspace_id=WORKSPACE,
            query_vector=[0.1] * EMBEDDING_DIMENSION,
            metadata_filter=MetadataFilter(),
            limit=10,
        )
        sql = _sql(statement)
        assert str(WORKSPACE) in sql
        assert str(OTHER_WORKSPACE) not in sql

    def test_vector_pass_uses_cosine_distance(self) -> None:
        statement = vector_statement(
            workspace_id=WORKSPACE,
            query_vector=[0.1] * EMBEDDING_DIMENSION,
            metadata_filter=MetadataFilter(),
            limit=10,
        )
        sql = _sql(statement)
        # pgvector's `<=>` operator is the cosine distance index path; the
        # label is `distance` on the SELECT.
        assert "<=>" in sql
        assert "AS distance" in sql
        assert "ORDER BY" in sql

    def test_vector_pass_skips_unindexed_chunks(self) -> None:
        """A corpus stored without vectors must return nothing, not everything."""
        statement = vector_statement(
            workspace_id=WORKSPACE,
            query_vector=[0.1] * EMBEDDING_DIMENSION,
            metadata_filter=MetadataFilter(),
            limit=10,
        )
        assert "embedding IS NOT NULL" in _sql(statement)

    def test_vector_pass_applies_metadata_filters(self) -> None:
        statement = vector_statement(
            workspace_id=WORKSPACE,
            query_vector=[0.1] * EMBEDDING_DIMENSION,
            metadata_filter=MetadataFilter(country_code="US"),
            limit=10,
        )
        sql = _sql(statement)
        assert "country_code" in sql
        assert "'US'" in sql

    def test_both_passes_join_chunks_to_their_document(self) -> None:
        for statement in (
            keyword_statement(
                workspace_id=WORKSPACE,
                query="x",
                metadata_filter=MetadataFilter(),
                limit=1,
            ),
            vector_statement(
                workspace_id=WORKSPACE,
                query_vector=[0.1] * EMBEDDING_DIMENSION,
                metadata_filter=MetadataFilter(),
                limit=1,
            ),
        ):
            assert "JOIN compliance_documents" in _sql(statement)

    def test_index_sql_targets_one_document_with_bound_parameters(self) -> None:
        """No interpolation: a hostile value must not reach the parser."""
        from app.rag.service import _INDEX_SQL

        sql = str(_INDEX_SQL)
        assert ":workspace_id" in sql
        assert ":document_id" in sql
        assert "to_tsvector" in sql


class TestMetadataFilter:
    def test_an_empty_filter_produces_no_predicate(self) -> None:
        from app.models.platform import ComplianceDocument

        assert MetadataFilter().is_empty
        assert MetadataFilter().apply(ComplianceDocument) is None

    def test_marketplace_filter_is_an_equality_predicate(self) -> None:
        """§14 filters must be pushed into SQL, not applied after retrieval."""
        from app.models.platform import ComplianceDocument

        predicate = MetadataFilter(marketplace_code="amazon").apply(
            ComplianceDocument
        )
        assert predicate is not None
        sql = str(predicate.compile(compile_kwargs={"literal_binds": True}))
        assert "marketplace_code" in sql
        assert "amazon" in sql

    def test_category_filter_matches_anywhere_in_the_path(self) -> None:
        from sqlalchemy.dialects import postgresql

        from app.models.platform import ComplianceDocument

        predicate = MetadataFilter(category_path="Shirts").apply(ComplianceDocument)
        assert predicate is not None
        sql = str(
            predicate.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        # Compiled against Postgres explicitly: the generic dialect renders
        # `ilike` as `lower(...) LIKE lower(...)`, which would pass a check for
        # "LIKE" while never proving the Postgres form is used.
        assert "ILIKE" in sql.upper()
        assert "%Shirts%" in sql

    def test_effective_date_filter_keeps_undated_rules(self) -> None:
        """Undated policy is still policy; hiding it removes a needed rule."""
        from app.models.platform import ComplianceDocument

        predicate = MetadataFilter(
            effective_on=datetime(2026, 1, 1, tzinfo=UTC)
        ).apply(ComplianceDocument)
        assert predicate is not None
        sql = str(predicate.compile(compile_kwargs={"literal_binds": True}))
        assert "IS NULL" in sql.upper()
        assert "<=" in sql

    def test_multiple_filters_are_anded(self) -> None:
        from app.models.platform import ComplianceDocument

        predicate = MetadataFilter(
            marketplace_code="amazon", country_code="US"
        ).apply(ComplianceDocument)
        assert predicate is not None
        sql = str(predicate.compile(compile_kwargs={"literal_binds": True}))
        assert sql.count(" AND ") == 1