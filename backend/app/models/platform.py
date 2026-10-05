"""Marketplace connectors, compliance RAG, and agent observability models."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    ImmutableTenantEntity,
    SoftDeletableTenantEntity,
    str_enum,
)


class ConnectionStatus(enum.StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ERROR = "error"


class MarketplaceConnection(SoftDeletableTenantEntity):
    """A marketplace credential set.

    Credentials are encrypted at rest with the per-environment Fernet key.
    A plaintext column would violate Section 33, so the model has no plain
    token field at all.
    """

    __tablename__ = "marketplace_connections"

    marketplace: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ConnectionStatus] = mapped_column(
        str_enum(ConnectionStatus, name="connection_status", length=32),
        default=ConnectionStatus.PENDING,
        nullable=False,
    )

    # Fernet ciphertext. Never logged, never returned by the API.
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False)
    # Non-secret metadata: which scopes were granted, so re-auth can be judged.
    granted_scopes: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "marketplace", "display_name", name="uq_connection_label"
        ),
        Index("ix_connections_workspace_marketplace", "workspace_id", "marketplace"),
        {"comment": "Marketplace credentials; encrypted at rest, never plaintext"},
    )


class ComplianceDocument(SoftDeletableTenantEntity):
    """A marketplace policy or rule document (Section 13).

    Metadata is stored as columns rather than only in the chunk payload
    because Section 14 requires filtering by marketplace, category, country,
    version, and effective date. Those filters are relational.
    """

    __tablename__ = "compliance_documents"

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Metadata lives in indexed columns, not only in chunk payloads. Section 14
    # requires filtering by marketplace, category, country, version, and
    # effective date; those are SQL-level predicates and must be indexed.
    marketplace_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    category_path_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rule_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version_label: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("workspace_id", "content_hash", name="uq_doc_content_hash"),
        Index("ix_docs_workspace_marketplace", "workspace_id", "marketplace_code"),
        Index("ix_docs_workspace_effective", "workspace_id", "effective_date"),
        {"comment": "Compliance source document with filterable metadata"},
    )


class ComplianceChunk(SoftDeletableTenantEntity):
    """A retrievable chunk with pgvector embeddings (D-006).

    Section 14 requires hybrid retrieval: semantic plus keyword plus metadata
    filter. The vector column covers semantic; `tsvector` covers keyword;
    the document join covers metadata. Vector-only retrieval is explicitly
    rejected as insufficient.
    """

    __tablename__ = "compliance_chunks"

    document_id: Mapped[UUID] = mapped_column(nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Section name or heading, so a recommendation can cite its source
    # (Section 13 requires citing the exact rule that supports a change).
    section_reference: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # pgvector column, dimension fixed by the chosen embedding model. Sized
    # for 1536 dims with headroom; D-006 depends on pgvector being enabled.
    embedding: Mapped[Any | None] = mapped_column(Vector(1536), nullable=True)
    # Keyword half of hybrid retrieval (Section 14). Semantic alone is
    # explicitly rejected: rule text contains exact identifiers, category
    # codes, and character limits that embedding similarity handles poorly.
    search_vector: Mapped[Any | None] = mapped_column(TSVECTOR, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["compliance_documents.workspace_id", "compliance_documents.id"],
            name="fk_chunks_workspace_document",
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "document_id", "chunk_index", name="uq_chunk_index"),
        Index("ix_chunks_workspace_document", "workspace_id", "document_id"),
        # Both retrieval indexes are declared once, here. This class previously
        # carried a second `__table_args__` block, which silently replaced this
        # one and dropped the GIN and HNSW indexes from both the model and the
        # generated migration -- leaving hybrid retrieval correct but running as
        # a sequential scan over every chunk in the workspace.
        Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
        {"comment": "Retrievable chunk; semantic plus keyword plus metadata"},
    )


class AgentRun(SoftDeletableTenantEntity):
    """A LangGraph execution (Section 32).

    Records the safe execution trace: nodes entered, tools called, outcomes.
    Never stores private chain-of-thought (Section 30). Only concise
    reasoning summaries the model was explicitly asked to produce.
    """

    __tablename__ = "agent_runs"

    run_type: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)
    # LangGraph checkpoint id, so an interrupted run can be resumed.
    checkpoint_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    input_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Node-level trace with durations. Safe to display in the UI.
    node_trace: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    llm_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    llm_tokens_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_agent_runs_workspace_started", "workspace_id", "started_at"),
        Index("ix_agent_runs_workspace_status", "workspace_id", "status"),
        {"comment": "Safe execution trace; excludes private chain-of-thought"},
    )


class ToolCall(ImmutableTenantEntity):
    """An individual tool invocation (Section 23).

    Every tool has a strict input and output schema, validation, error
    handling, logging, and an authorization check. This table is the record
    that those checks ran.
    """

    __tablename__ = "tool_calls"

    agent_run_id: Mapped[UUID] = mapped_column(nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Masked at the edge (D-010). Raw catalog values never land here.
    arguments: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "agent_run_id"],
            ["agent_runs.workspace_id", "agent_runs.id"],
            name="fk_tool_calls_workspace_run",
            ondelete="CASCADE",
        ),
        Index("ix_tool_calls_workspace_run", "workspace_id", "agent_run_id"),
        Index("ix_tool_calls_workspace_name", "workspace_id", "tool_name"),
        {"comment": "Tool invocation record; arguments masked at the edge"},
    )


class LlmUsage(ImmutableTenantEntity):
    """Per-call cost accounting (Section 56, D-010).

    Built in-house because D-010 prohibits vendor dashboards. Token counts
    include repair-loop retries, which D-007 requires be counted.
    """

    __tablename__ = "llm_usage"

    agent_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    purpose: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Repair attempts after schema validation failure (D-007).
    repair_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_cost_usd: Mapped[float] = mapped_column(nullable=False)
    # True when the call came from a retry, so cost anomalies are explainable.
    was_retry: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_llm_usage_workspace_created", "workspace_id", "created_at"),
        Index("ix_llm_usage_workspace_model", "workspace_id", "model"),
        CheckConstraint("estimated_cost_usd >= 0", name="cost_non_negative"),
        {"comment": "Per-call token and cost accounting"},
    )