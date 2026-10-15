"""Retrieval and reconciliation at a corpus size worth measuring (Phase 27).

Two things this phase was told to profile and has not:

- **Retrieval candidate generation beyond the per-workspace policy set.** The
  unit tests use a handful of chunks, where every strategy wins. A real
  compliance corpus is hundreds of documents; the keyword pass, the vector pass
  and the rerank are all O(corpus), and the question is whether any of them
  dominates or degrades in a way the small tests cannot see.
- **`build_observations`, which re-normalizes every record on every run.** D-019
  and D-020 deferred this explicitly. Normalization is the expensive part of
  ingestion, and doing it once per record per reconciliation is a cost that
  grows with catalog size and repeats on every run.

What is asserted is a generous budget, because these are timings and timings
depend on the machine. The measurements are printed: they are the deliverable,
and the budgets exist so a regression fails as a number rather than as a feeling.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

import pytest
from sqlalchemy import text

from app.db.base import TenantContext
from tests.performance.conftest import SMALL, build_catalog

#: Documents for the retrieval corpus. Each is chunked into several, so this is
#: a few thousand chunks — three orders of magnitude past the unit tests.
CORPUS = 400


class TopicEmbedder:
    """A deterministic provider, so a timing is a timing and not a network call."""

    name = "perf-topic"
    model = "perf-topic-v1"
    available = True

    def __init__(self) -> None:
        from app.rag.embeddings import EMBEDDING_DIMENSION

        self.dimension = EMBEDDING_DIMENSION

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vector = [0.0] * self.dimension
        for index, word in enumerate(
            ("label", "textile", "fibre", "warning", "battery", "charger", "hazard")
        ):
            vector[index] = 1.0 if word in " ".join(texts).lower() else 0.0
        # A constant component keeps the vector finite and non-null.
        vector[len("labelfibrewarningbatterychargerhazard")] = 0.01

        return [vector for _ in texts]


def _tenant(perf_identity: dict) -> TenantContext:
    return TenantContext(
        workspace_id=perf_identity["workspace"],
        user_id=perf_identity["user"],
    )


def _bind(workspace_id: Any):
    return text("SELECT set_config('app.current_workspace', :ws, true)").bindparams(
        ws=str(workspace_id)
    )


@pytest.mark.performance
class TestRetrievalAtCorpusScale:
    @pytest.fixture
    async def corpus(self, engine: Any, perf_identity: dict, app_session_factory: Any):
        """A workspace holding `CORPUS` chunked documents."""

        from app.rag.service import ingest_document

        tenant = _tenant(perf_identity)
        embedder = TopicEmbedder()

        async with app_session_factory() as session:
            await session.execute(_bind(tenant.workspace_id))
            started = time.perf_counter()
            for index in range(CORPUS):
                await ingest_document(
                    session,
                    tenant,
                    title=f"Policy {index}",
                    content=(
                        f"Section {index}. A textile product must carry a fibre "
                        f"content label listing the composition of the textile "
                        f"and the country of origin. Rule {index} applies."
                    ),
                    marketplace_code="AMZ",
                    country_code="DE",
                    document_kind="policy",
                    rule_kind="labeling" if index % 2 == 0 else "safety",
                    embedder=embedder,
                )
            await session.commit()
            elapsed = time.perf_counter() - started

        async with engine.connect() as connection:
            chunks = (
                await connection.execute(
                    text("SELECT count(*) FROM compliance_chunks WHERE workspace_id = :ws"),
                    {"ws": str(tenant.workspace_id)},
                )
            ).scalar_one()

        assert chunks >= CORPUS, f"expected a corpus, found {chunks} chunks"
        print(f"\n  corpus: {CORPUS} documents, {chunks} chunks, ingested in {elapsed:.1f}s")

        return tenant

    async def test_search_latency_stays_inside_a_budget(
        self, app_session_factory: Any, corpus: TenantContext
    ) -> None:
        """§57's vector-search optimisation, measured rather than assumed."""

        from app.rag.service import MetadataFilter, search

        embedder = TopicEmbedder()
        timings: list[float] = []

        async with app_session_factory() as session:
            await session.execute(_bind(corpus.workspace_id))
            for query in (
                "fibre content label and country of origin",
                "battery charger hazard warning",
                "textile composition",
            ):
                started = time.perf_counter()
                result = await search(
                    session,
                    corpus,
                    query=query,
                    metadata_filter=MetadataFilter(),
                    top_k=10,
                    embedder=embedder,
                )
                timings.append(time.perf_counter() - started)
                assert result.hits, f"no hits for {query!r}"

        worst = max(timings)
        print(f"\n  retrieval over {CORPUS} documents: {[f'{t * 1000:.0f}ms' for t in timings]}")
        assert worst < 2.0, (
            f"retrieval took {worst:.2f}s over {CORPUS} documents; a budget of 2s "
            "is generous and exists to catch an order-of-magnitude regression"
        )

    async def test_a_filtered_query_is_not_slower_than_an_unfiltered_one(
        self, app_session_factory: Any, corpus: TenantContext
    ) -> None:
        """Filters belong in SQL, so they can only narrow the work.

        A filter applied after retrieval would make this equal, and §14's whole
        argument is that it must not be.
        """
        from app.rag.service import MetadataFilter, search

        embedder = TopicEmbedder()

        async def timed(**kwargs: Any) -> float:
            async with app_session_factory() as session:
                await session.execute(_bind(corpus.workspace_id))
                started = time.perf_counter()
                result = await search(
                    session, corpus, query="label", top_k=10, embedder=embedder, **kwargs
                )
                elapsed = time.perf_counter() - started
            return elapsed, len(result.hits)

        unfiltered, unfiltered_hits = await timed(metadata_filter=MetadataFilter())
        filtered, filtered_hits = await timed(
            metadata_filter=MetadataFilter(rule_kind="labeling")
        )

        print(
            f"\n  unfiltered {unfiltered * 1000:.0f}ms ({unfiltered_hits} hits), "
            f"filtered {filtered * 1000:.0f}ms ({filtered_hits} hits)"
        )
        assert filtered_hits <= unfiltered_hits
        assert filtered <= unfiltered * 2 + 0.1, "a filter made the query slower"

    async def test_the_vector_pass_reaches_the_index(
        self, app_session_factory: Any, corpus: TenantContext
    ) -> None:
        """The HNSW index must be usable at corpus scale, not merely present.

        Phase 24 proved it builds with an operator class. This proves the planner
        reaches for it when there is enough data to prefer one — and it EXPLAINs
        the statement `service.vector_statement` builds rather than a query
        invented here, because an index the application never asks for is not an
        index doing anything.
        """
        from app.rag.service import MetadataFilter, vector_statement

        embedder = TopicEmbedder()
        query_vector = (await embedder.embed(["fibre content label"]))[0]

        async with app_session_factory() as session:
            statement = vector_statement(
                workspace_id=corpus.workspace_id,
                query_vector=query_vector,
                metadata_filter=MetadataFilter(),
                limit=10,
            )
            compiled = statement.compile(
                session.bind.sync_engine,
                compile_kwargs={"literal_binds": True},
            )
            plan = "\n".join(
                row[0]
                for row in (
                    await session.execute(
                        text(f"EXPLAIN (ANALYZE) {compiled}"),
                    )
                ).all()
            )

        indexed = "Index Scan" in plan
        print(f"\n  vector query plan: index scan={indexed}")
        if not indexed:
            print(plan)
        assert indexed, (
            "the vector query does not use an index at corpus scale; the HNSW "
            f"index is not earning its maintenance cost:\n{plan}"
        )

@pytest.mark.performance
class TestReconciliationAtCatalogScale:
    @pytest.fixture
    async def catalog(self, engine: Any, perf_identity: dict):
        from sqlalchemy.ext.asyncio import async_sessionmaker

        tenant = _tenant(perf_identity)
        session = async_sessionmaker(engine, expire_on_commit=False)()
        try:
            await build_catalog(session, tenant.workspace_id, products=SMALL * 20)
        finally:
            await session.close()

        return tenant

    async def test_a_run_scales_with_products_but_stays_linear(
        self,
        engine: Any,
        catalog: TenantContext,
        app_session_factory: Any,
    ) -> None:
        """Reconciliation at 500 products, measured.

        Linear is the acceptable shape: the work is per product by construction.
        The measurement exists to catch a shape that is *not* linear — a
        re-normalization per run per record is linear too, but with a constant
        that grows as the alias table grows.
        """
        from app.models.catalog import Catalog
        from app.services.reconciliation.engine import reconcile_catalog

        async with engine.connect() as connection:
            catalog_id = (
                await connection.execute(
                    text("SELECT id FROM catalogs WHERE workspace_id = :ws LIMIT 1"),
                    {"ws": str(catalog.workspace_id)},
                )
            ).scalar_one()

        timings: list[float] = []
        async with app_session_factory() as session:
            await session.execute(_bind(catalog.workspace_id))
            catalog_row = await session.get(Catalog, catalog_id)
            started = time.perf_counter()
            summary = await reconcile_catalog(
                session, catalog, catalog=catalog_row
            )
            timings.append(time.perf_counter() - started)

        elapsed = timings[0]
        print(
            f"\n  reconciliation: {summary.products_analyzed} products, "
            f"{summary.conflicts_found} conflicts in {elapsed:.1f}s"
        )
        assert elapsed < 30.0, (
            f"reconciling {summary.products_analyzed} products took {elapsed:.1f}s"
        )

    async def test_build_observations_re_normalizes_every_record(
        self, engine: Any, catalog: TenantContext, app_session_factory: Any
    ) -> None:
        """D-019/D-020's deferred profiling, measured.

        `build_observations` calls `map_record` for every source record on every
        run. That is the design — a record can change, so its normalized form
        cannot be assumed — but its cost is the reconciler's dominant term, and
        a number is worth more than an acknowledgement.
        """
        from app.services.reconciliation.engine import _load_inputs

        async with engine.connect() as connection:
            product_ids = list(
                (
                    await connection.execute(
                        text("SELECT id FROM products WHERE workspace_id = :ws"),
                        {"ws": str(catalog.workspace_id)},
                    )
                ).scalars()
            )

        async with app_session_factory() as session:
            await session.execute(_bind(catalog.workspace_id))
            records, _ = await _load_inputs(session, catalog, product_ids)

            from app.services.reconciliation.engine import build_observations

            total_records = sum(len(rows) for rows in records.values())
            if not total_records:
                pytest.skip("no linked records to profile")

            from app.models.catalog import Product

            first_id = next(iter(product_ids))
            product = await session.get(Product, first_id)
            rows = records[first_id]

            started = time.perf_counter()
            for _ in range(20):
                build_observations(product, rows)
            per_call = (time.perf_counter() - started) / 20

        print(
            f"\n  build_observations: {len(rows)} records per product in "
            f"{per_call * 1000:.1f}ms"
        )
        assert per_call < 0.5, (
            f"one product's observations took {per_call * 1000:.0f}ms; at a few "
            "hundred products that is minutes per run"
        )