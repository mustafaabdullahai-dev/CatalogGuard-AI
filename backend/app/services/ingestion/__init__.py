"""Catalog ingestion service (Phase 10, §49).

The intake pipeline: validation -> parsing -> schema detection -> raw storage.

Normalization to canonical products is deliberately a *separate* pass
(Phase 11). Ingestion writes `source_records` verbatim and nothing else, so the
raw record always survives and every later decision -- normalization, identity,
conflict resolution -- can be re-derived from it if the rules change.
"""

from app.services.ingestion.errors import IngestionError, IngestionErrorCode
from app.services.ingestion.formats import ParsedFile, parse_file
from app.services.ingestion.pipeline import (
    IngestOutcome,
    PersistedIngestion,
    PreparedIngestion,
    PreparedRecord,
    ingest,
    persist,
    plan_execution,
    prepare,
)
from app.services.ingestion.schema import (
    ColumnProfile,
    SchemaDetection,
    detect_schema,
)
from app.services.ingestion.security import ValidatedFile, validate_file

__all__ = [
    "ColumnProfile",
    "IngestOutcome",
    "IngestionError",
    "IngestionErrorCode",
    "ParsedFile",
    "PersistedIngestion",
    "PreparedIngestion",
    "PreparedRecord",
    "SchemaDetection",
    "ValidatedFile",
    "detect_schema",
    "ingest",
    "parse_file",
    "persist",
    "plan_execution",
    "prepare",
    "validate_file",
]
