"""Tenant-scoped repositories for compliance knowledge (D-011)."""

from __future__ import annotations

from app.db.repository import TenantRepository
from app.models.platform import ComplianceChunk, ComplianceDocument


class ComplianceDocumentRepository(TenantRepository[ComplianceDocument]):
    model = ComplianceDocument


class ComplianceChunkRepository(TenantRepository[ComplianceChunk]):
    model = ComplianceChunk
