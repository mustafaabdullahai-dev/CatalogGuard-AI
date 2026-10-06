"""Hybrid retrieval (§14).

§14 is explicit: *"Avoid naïve vector-only retrieval."* Rule text is full of
exact identifiers, category codes, character limits and negations — the things
embeddings are worst at. So retrieval takes two passes and combines them, and
every result reports **which signals supported it**: "the model felt this was
relevant" and "this page contains the limit you asked about" are different claims,
and a reviewer deciding whether to trust a citation needs to tell them apart.

Ranking is deterministic. A vector pass may *supply* candidates, but once they
exist the ordering is arithmetic over numbers already in hand, so the same query
against the same corpus returns the same order tomorrow.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.rag.chunking import keywords

# Per-signal weights, normalized over whichever signals actually fired. That
# normalization is what stops a pure-semantic hit being scored out of 0.6 and
# losing to a mediocre keyword hit scored out of 0.4.
SEMANTIC_WEIGHT = 0.6
KEYWORD_WEIGHT = 0.4

# Added when the whole query phrase appears verbatim: an exact match on a rule is
# a stronger claim than the same words scattered through a paragraph.
_PHRASE_BONUS = 0.25

# Bounds on how many terms take part in a comparison. Beyond these the extra
# terms are function words that cost time and separate nothing.
_QUERY_TERMS = 40
_CONTENT_TERMS = 300


@dataclass(frozen=True, slots=True)
class Candidate:
    """A chunk offered for ranking by either retrieval pass."""

    chunk_id: UUID
    document_id: UUID
    content: str
    section_reference: str | None
    document_title: str
    marketplace_code: str | None = None
    similarity: float | None = None
    keyword_rank: int | None = None


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    chunk_id: UUID
    document_id: UUID
    content: str
    section_reference: str | None
    document_title: str
    marketplace_code: str | None
    score: float
    signals: tuple[str, ...]

    @property
    def citation(self) -> dict[str, object]:
        """The exact rule a recommendation can point at (§13)."""
        return {
            "document_id": str(self.document_id),
            "document_title": self.document_title,
            "section_reference": self.section_reference,
            "marketplace": self.marketplace_code,
            "signals": list(self.signals),
            "score": round(self.score, 4),
        }


def lexical_score(query: str, query_terms: Sequence[str], content: str) -> float:
    """Fraction of the query's terms present in `content`, plus a phrase bonus.

    Term overlap rather than tf-idf: the corpus is small and per-workspace, and
    already filtered by metadata, so ranking needs to explain itself to a human
    ("3 of your 4 terms appear here") more than it needs statistical weighting.
    """
    if not query_terms:
        return 0.0
    content_terms = set(keywords(content, limit=_CONTENT_TERMS))
    matched = sum(1 for term in query_terms if term in content_terms)
    score = matched / len(query_terms)
    if score and query and query.casefold().strip() in content.casefold():
        score = min(1.0, score + _PHRASE_BONUS)
    return score


def combine(*, semantic: float | None, lexical: float) -> tuple[float, tuple[str, ...]]:
    """Merge the signals that actually fired, and name them."""
    fired: list[tuple[str, float, float]] = []
    if semantic is not None:
        fired.append(("semantic", semantic, SEMANTIC_WEIGHT))
    if lexical > 0:
        fired.append(("keyword", lexical, KEYWORD_WEIGHT))
    if not fired:
        return 0.0, ()

    total_weight = sum(weight for _, _, weight in fired)
    score = sum(value * weight for _, value, weight in fired) / total_weight
    return score, tuple(name for name, _, _ in fired)


def rerank(
    query: str, candidates: Sequence[Candidate], *, top_k: int
) -> list[RetrievalHit]:
    """Score and order candidates, keeping only those with any evidence.

    Determinism comes from the tie-break: after score, the keyword pass's own rank
    settles it and chunk id settles anything left. Sorting a plain `str` id rather
    than a UUID object keeps the order independent of how UUIDs compare.
    """
    terms = keywords(query, limit=_QUERY_TERMS)
    scored: list[tuple[float, int, Candidate, tuple[str, ...]]] = []

    for candidate in candidates:
        lexical = lexical_score(query, terms, candidate.content)
        score, signals = combine(semantic=candidate.similarity, lexical=lexical)
        if not signals:
            # No evidence from either pass. Dropping it beats shipping a result
            # whose only merit is having been in the candidate set.
            continue
        scored.append((score, candidate.keyword_rank or 0, candidate, signals))

    scored.sort(key=lambda item: (-item[0], item[1], str(item[2].chunk_id)))

    return [
        RetrievalHit(
            chunk_id=candidate.chunk_id,
            document_id=candidate.document_id,
            content=candidate.content,
            section_reference=candidate.section_reference,
            document_title=candidate.document_title,
            marketplace_code=candidate.marketplace_code,
            score=score,
            signals=signals,
        )
        for score, _rank, candidate, signals in scored[:top_k]
    ]


def signals_used(*, embedder_available: bool) -> tuple[str, ...]:
    """What this deployment can search with, stated plainly.

    Returned to the caller so a response can say "keyword + metadata only" instead
    of looking identical to a semantically-indexed one.
    """
    signals = ["keyword", "metadata"]
    if embedder_available:
        signals.insert(0, "semantic")
    return tuple(signals)

