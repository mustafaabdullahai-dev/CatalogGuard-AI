"""Ingestion repositories.

Re-exported from the catalog domain rather than redefined, because
`catalog_sources` and `source_records` are catalog tables. Defining them here and
again in `services.catalog` would be two bindings of one model, and the day one
of them changed the other would silently keep the old behaviour.
"""

from __future__ import annotations

from app.services.catalog.repositories import (
    CatalogSourceRepository,
    SourceRecordRepository,
)

__all__ = ["CatalogSourceRepository", "SourceRecordRepository"]
