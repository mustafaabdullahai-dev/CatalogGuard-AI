"""Ingestion pipeline orchestration (§49, §28).

Wires the steps together and decides whether a file is parsed in the request or
handed to the worker. The decision is isolated in `plan_execution`, which is a
pure function of file size and configuration, so the sync/async boundary is
testable without a broker and without a database.

Storage writes raw `source_records` only. A `CatalogSource` row is the identity
of the feed; the records are what it delivered. Turning those into canonical
products is Phase 11's job, deliberately not this one's, so nothing here can
corrupt a product while merely trying to capture a file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.base import TenantContext, utcnow
from app.models.catalog import CatalogSource, SourceRecord
from app.services.ingestion import formats, security
from app.services.ingestion.errors import IngestionError, IngestionErrorCode
from app.services.ingestion.formats import ParsedFile
from app.services.ingestion.repositories import SourceRecordRepository
from app.services.ingestion.schema import (
    IDENTITY_FIELDS,
    SchemaDetection,
    detect_schema,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedRecord:
    """One raw row, plus the external id it will be stored under."""

    external_id: str
    raw_data: dict[str, object]


@dataclass(frozen=True, slots=True)
class PreparedIngestion:
    """Everything derived from a file before a single row is written."""

    validated: security.ValidatedFile
    parsed: ParsedFile
    schema: SchemaDetection
    records: tuple[PreparedRecord, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PersistedIngestion:
    source_id: UUID
    records_inserted: int


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    """The result of handing a file to the pipeline."""

    execution: Literal["inline", "queued"]
    prepared: PreparedIngestion
    persisted: PersistedIngestion | None = None
    message_id: str | None = None


def plan_execution(size_bytes: int) -> Literal["inline", "queued"]:
    """Whether this file is parsed now or enqueued for the worker.

    `queued` requires both `dramatiq` mode and a file above the inline threshold.
    In `inline` mode every file is parsed in the request even if it is large,
    which is the honest default while no worker runs: a queue with no consumer
    would leave an upload looking accepted and never processed.
    """
    settings = get_settings()
    if (
        settings.ingestion_execution == "dramatiq"
        and size_bytes > settings.ingestion_inline_max_bytes
    ):
        return "queued"
    return "inline"


def prepare(filename: str, content: bytes) -> PreparedIngestion:
    """Validate, parse and profile a file. Touches no database.

    Pure by design: the expensive, failure-prone half of ingestion is provable
    from bytes alone, so it is tested without Postgres or Redis.
    """
    validated = security.validate_file(filename, content)
    parsed = formats.parse_file(validated.extension, content)
    schema = detect_schema(parsed.rows, parsed.columns)
    records = _build_records(parsed.rows, schema)
    warnings = (*parsed.warnings, *schema.warnings)
    return PreparedIngestion(
        validated=validated,
        parsed=parsed,
        schema=schema,
        records=records,
        warnings=warnings,
    )


def _build_records(
    rows: tuple[dict[str, object], ...], schema: SchemaDetection
) -> tuple[PreparedRecord, ...]:
    """Assign each row an external id for its source.

    The id is derived from the first identity column the detector found (gtin,
    then upc, then mpn, then sku, in detected order), falling back to the row
    number. Duplicates within one file are suffixed rather than allowed to
    collide, because `uq_source_record` would reject the second row at the
    database and abort the whole upload for one repeated identifier.
    """
    canonical_to_column = {
        column.canonical: column.name
        for column in schema.columns
        if column.canonical is not None
    }
    identity_order = [
        column.canonical
        for column in schema.columns
        if column.canonical in IDENTITY_FIELDS
    ]

    seen: dict[str, int] = {}
    records: list[PreparedRecord] = []
    for index, row in enumerate(rows):
        external_id: str | None = None
        for field in identity_order:
            column_name = canonical_to_column.get(field)
            value = row.get(column_name) if column_name else None
            if value not in (None, ""):
                external_id = str(value)
                break
        if external_id is None:
            external_id = f"row-{index + 1}"
        if external_id in seen:
            seen[external_id] += 1
            external_id = f"{external_id}#{seen[external_id]}"
        else:
            seen[external_id] = 1
        records.append(PreparedRecord(external_id=external_id, raw_data=dict(row)))
    return tuple(records)


async def persist(
    db: AsyncSession,
    tenant: TenantContext,
    prepared: PreparedIngestion,
    *,
    source: CatalogSource,
) -> PersistedIngestion:
    """Write the raw records and stamp the source with what it delivered.

    Refuses a source from another workspace even though RLS would also block it.
    Two independent checks for the one thing that must never be wrong is not
    redundant; it is the difference between a bug and a breach.
    """
    if source.workspace_id is not None and source.workspace_id != tenant.workspace_id:
        raise IngestionError(
            code=IngestionErrorCode.STORAGE_FAILED,
            message="the target catalog source belongs to a different workspace",
            recoverable=False,
        )

    records_repo = SourceRecordRepository(db, tenant)
    stamp = utcnow()

    source.detected_schema = prepared.schema.as_dict()
    source.last_sequence_timestamp = stamp
    source.authorization_context = {
        "method": "upload",
        "filename": prepared.validated.filename,
        "format": prepared.parsed.format,
        "encoding": prepared.parsed.encoding,
        "scanned_by": prepared.validated.scanner,
        "row_count": prepared.parsed.row_count,
        "truncated": prepared.parsed.truncated,
    }

    for record in prepared.records:
        await records_repo.add(
            SourceRecord(
                catalog_source_id=source.id,
                external_id=record.external_id,
                raw_data=record.raw_data,
                sequence_timestamp=stamp,
            )
        )

    await db.flush()
    logger.info(
        "ingestion_stored",
        workspace_id=str(tenant.workspace_id),
        source_id=str(source.id),
        records=len(prepared.records),
        format=prepared.parsed.format,
    )
    return PersistedIngestion(
        source_id=source.id, records_inserted=len(prepared.records)
    )


async def ingest(
    db: AsyncSession,
    tenant: TenantContext,
    *,
    filename: str,
    content: bytes,
    source: CatalogSource,
) -> IngestOutcome:
    """Route a file to inline parsing or the worker, per `plan_execution`."""
    prepared = prepare(filename, content)
    if plan_execution(len(content)) == "queued":
        message_id = _enqueue(
            workspace_id=tenant.workspace_id,
            source_id=source.id,
            prepared=prepared,
            content=content,
        )
        return IngestOutcome(
            execution="queued", prepared=prepared, message_id=message_id
        )
    persisted = await persist(db, tenant, prepared, source=source)
    return IngestOutcome(execution="inline", prepared=prepared, persisted=persisted)


def _enqueue(
    *,
    workspace_id: UUID,
    source_id: UUID,
    prepared: PreparedIngestion,
    content: bytes,
) -> str:
    """Persist the upload and hand it to Dramatiq.

    The broker and the actor are imported here rather than at module import, so
    the API does not configure a Redis broker merely by loading the ingestion
    service. That keeps `inline` mode -- the default -- fully offline.
    """
    from app.services.ingestion import storage
    from app.workers.broker import get_broker
    from app.workers.ingestion import ingest_stored_file

    path = storage.store_upload(workspace_id, prepared.validated.extension, content)
    get_broker()
    message = ingest_stored_file.send(
        str(workspace_id),
        str(source_id),
        str(path),
        prepared.validated.filename,
    )
    logger.info(
        "ingestion_enqueued",
        workspace_id=str(workspace_id),
        source_id=str(source_id),
        stored_path=str(path),
        message_id=message.message_id,
    )
    return message.message_id

