"""Ingestion request and response models (Phase 10).

Response models are built field by field from the domain objects rather than by
dumping an ORM row. `CatalogSource.authorization_context` and
`MarketplaceConnection.encrypted_credentials` are the reason: a default dump
exposes whatever a future migration adds, and the two that exist today are the
ones that must never leave the process.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.catalog import SourceType


class CatalogSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    source_type: SourceType = Field(
        description="Where the data comes from; drives source authority (D-002)."
    )


class CatalogSourceRef(BaseModel):
    id: UUID
    name: str
    source_type: str
    created_at: datetime
    last_sequence_timestamp: datetime | None
    record_count: int = 0

    @classmethod
    def from_record(cls, source: Any, *, record_count: int = 0) -> CatalogSourceRef:
        return cls(
            id=source.id,
            name=source.name,
            source_type=str(source.source_type),
            created_at=source.created_at,
            last_sequence_timestamp=source.last_sequence_timestamp,
            record_count=record_count,
        )


class CatalogSourceListResponse(BaseModel):
    sources: list[CatalogSourceRef]


class ColumnProfileModel(BaseModel):
    name: str
    canonical: str | None
    inferred_type: str
    null_rate: float
    distinct_count: int
    is_unique: bool
    sample_values: list[str]


class SchemaModel(BaseModel):
    """The detected schema, as shown to a reviewer before normalization."""

    row_count: int
    columns: list[ColumnProfileModel]
    canonical_fields: list[str]
    unknown_columns: list[str]
    identity_columns: list[str]
    suggested_aliases: dict[str, str]
    warnings: list[str]

    @classmethod
    def from_detection(cls, detection: Any) -> SchemaModel:
        return cls.model_validate(detection.as_dict())


class UploadResponse(BaseModel):
    source_id: UUID
    # `inline` means the rows are stored; `queued` means a worker will store
    # them. The distinction is stated so a client never reports success for a
    # file that has only been accepted.
    execution: str
    records_inserted: int
    message_id: str | None = None
    format: str
    encoding: str | None
    truncated: bool
    scanner: str
    warnings: list[str]
    detected_schema: SchemaModel


class SourceRecordRef(BaseModel):
    id: UUID
    external_id: str
    sequence_timestamp: datetime | None
    raw_data: dict[str, Any]

    @classmethod
    def from_record(cls, record: Any) -> SourceRecordRef:
        return cls(
            id=record.id,
            external_id=record.external_id,
            sequence_timestamp=record.sequence_timestamp,
            raw_data=dict(record.raw_data or {}),
        )


class SourceRecordListResponse(BaseModel):
    records: list[SourceRecordRef]
    total: int
    limit: int
    offset: int


class IngestionErrorResponse(BaseModel):
    """§54 error envelope."""

    error_code: str
    message: str
    details: dict[str, Any]
    recoverable: bool
