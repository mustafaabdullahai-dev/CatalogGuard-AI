"""Catalog ingestion worker (§28).

Consumes the `ingestion` queue for files too large to parse inline. The actor is
registered on the Redis broker at import, and this module is imported only from
`pipeline._enqueue`, so the API process never configures a broker it does not use.

Dramatiq retries a failed message. That is safe here because persistence is
idempotent per `(workspace, source, external_id)`: `uq_source_record` turns a
repeat write into a constraint conflict rather than a duplicate row. Retrying a
message that already succeeded is therefore harmless.

Not verified against a live Redis. No worker runs in this environment, so this
path is exercised through unit tests of the functions it calls, not end to end.
Treat it as implemented-but-unverified until a broker is reachable.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import dramatiq

from app.core.logging import get_logger
from app.db.base import TenantContext
from app.db.session import tenant_scope
from app.models.catalog import CatalogSource
from app.services.ingestion import storage
from app.services.ingestion.pipeline import persist, prepare
from app.workers.broker import get_broker

# Registering the actor requires a broker to be set. Importing `get_broker` here
# rather than in the API keeps that side effect on the worker side of the seam.
get_broker()

logger = get_logger(__name__)


@dramatiq.actor(queue_name="ingestion", max_retries=3)
def ingest_stored_file(
    workspace_id: str, source_id: str, stored_path: str, filename: str
) -> None:
    """Parse and store a file that was written to disk by `_enqueue`."""
    asyncio.run(_run(workspace_id, source_id, stored_path, filename))


async def _run(
    workspace_id: str, source_id: str, stored_path: str, filename: str
) -> None:
    content = storage.load_upload(stored_path)
    prepared = prepare(filename, content)
    workspace_uuid = UUID(workspace_id)
    tenant = TenantContext(workspace_id=workspace_uuid)

    async with tenant_scope(workspace_uuid) as db:
        source = await db.get(CatalogSource, UUID(source_id))
        if source is None:
            # Nothing to attach to. Logged and dropped rather than retried:
            # a missing source will not appear on the next attempt either.
            logger.error(
                "ingestion_source_missing",
                workspace_id=workspace_id,
                source_id=source_id,
            )
            return
        result = await persist(db, tenant, prepared, source=source)
        logger.info(
            "ingestion_worker_complete",
            workspace_id=workspace_id,
            source_id=source_id,
            records=result.records_inserted,
        )
