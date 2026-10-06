"""Compliance request and response models (Phase 13, §13, §14).

Every search response states **which signals produced it** and **which document
each hit came from**. §13 requires the AI to identify the exact rule supporting a
recommendation, so a bare list of paragraph strings would not satisfy it: the
caller needs the document, the section inside it, and the fact that the match was
lexical rather than semantic before it can weight the citation.

`signals` is included on every response for the same reason. A deployment running
without embeddings returns `keyword, metadata`, and the UI is expected to say so
rather than render an identical-looking answer it cannot stand behind.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.rag.retrieval import RetrievalHit


class ComplianceDocumentCreate(BaseModel):
    """A marketplace policy or rule document."""

    title: str = Field(
        min_length=1,
        max_length=512,
        description="Human-readable name; also the citation label.",
    )
    content: str = Field(
        min_length=1,
        description=(
            "Full document text. Markdown headings and 'Section 12.3' style "
            "lines become the section references citations point at."
        ),
    )
    source_url: str | None = Field(default=None, max_length=2048)
    marketplace_code: str | None = Field(default=None, max_length=64)
    country_code: str | None = Field(default=None, max_length=8)
    category_path: str | None = Field(
        default=None,
        max_length=1024,
        description="Category path, e.g. 'Clothing > Shirts > Men's Shirts'.",
    )
    document_kind: str | None = Field(default=None, max_length=64)
    rule_kind: str | None = Field(default=None, max_length=64)
    version_label: str | None = Field(default=None, max_length=64)
    effective_date: datetime | None = None


class ComplianceDocumentRef(BaseModel):
    id: UUID
    title: str
    source_url: str | None
    marketplace_code: str | None
    country_code: str | None
    category_path_text: str | None
    document_kind: str | None
    rule_kind: str | None
    version_label: str | None
    effective_date: datetime | None
    chunk_count: int = 0
    created_at: datetime

    @classmethod
    def from_record(cls, row: Any, *, chunk_count: int = 0) -> ComplianceDocumentRef:
        return cls(
            id=row.id,
            title=row.title,
            source_url=row.source_url,
            marketplace_code=row.marketplace_code,
            country_code=row.country_code,
            category_path_text=row.category_path_text,
            document_kind=row.document_kind,
            rule_kind=row.rule_kind,
            version_label=row.version_label,
            effective_date=row.effective_date,
            chunk_count=chunk_count,
            created_at=row.created_at,
        )


class ComplianceDocumentListResponse(BaseModel):
    documents: list[ComplianceDocumentRef]
    total: int
    limit: int
    offset: int


class ComplianceIngestResponse(BaseModel):
    document_id: UUID
    chunks_written: int
    token_total: int
    indexed_semantically: bool
    duplicate: bool
    note: str

    @classmethod
    def from_result(cls, result: Any, *, note: str) -> ComplianceIngestResponse:
        return cls(
            document_id=result.document_id,
            chunks_written=result.chunks_written,
            token_total=result.token_total,
            indexed_semantically=result.indexed_semantically,
            duplicate=result.duplicate,
            note=note,
        )


class ComplianceHitModel(BaseModel):
    """One retrieved chunk, carrying its own citation."""

    chunk_id: UUID
    document_id: UUID
    document_title: str
    section_reference: str | None
    marketplace_code: str | None
    content: str
    score: float
    signals: list[str]

    @classmethod
    def from_hit(cls, hit: RetrievalHit) -> ComplianceHitModel:
        return cls(
            chunk_id=hit.chunk_id,
            document_id=hit.document_id,
            document_title=hit.document_title,
            section_reference=hit.section_reference,
            marketplace_code=hit.marketplace_code,
            content=hit.content,
            score=round(hit.score, 4),
            signals=list(hit.signals),
        )


class ComplianceSearchResponse(BaseModel):
    query: str
    hits: list[ComplianceHitModel]
    signals: list[str]
    candidates_considered: int
    note: str

    @classmethod
    def from_result(
        cls, result: Any, *, note: str
    ) -> ComplianceSearchResponse:
        return cls(
            query=result.query,
            hits=[ComplianceHitModel.from_hit(hit) for hit in result.hits],
            signals=list(result.signals),
            candidates_considered=result.candidates_considered,
            note=note,
        )