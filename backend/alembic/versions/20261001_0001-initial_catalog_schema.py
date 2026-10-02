"""Initial catalog schema.

GENERATED FILE. Do not edit by hand. Regenerate, do not reformat.

Regenerate with:
    .venv/bin/python -m scripts.generate_initial_migration

Source of truth is `app.models`. Three concerns are not expressible in
the ORM and are emitted from `app.db.rls` and the generator constants:
  - the pgvector extension (D-006)
  - the audit_logs immutability triggers (D-013)
  - row-level security policies (D-011)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

from alembic import op
from app.db.base import Base
from app.db.rls import (
    GLOBAL_TABLES,
    create_policy_sql,
    drop_policy_sql,
    enable_rls_sql,
)

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# D-006: embeddings and near-duplicate detection. Neon does not enable
# pgvector by default.
op.execute("CREATE EXTENSION IF NOT EXISTS vector")

# Enum values, reflected from the ORM. These are VARCHAR columns
# with CHECK constraints rather than Postgres enum types, so adding a
# value is a migration like any other column change.
_ENUM_VALUES = {
    "approval_decision": ("approved", "modified", "re_analysis_requested", "rejected"),
    "conflict_type": (
        "attribute_mismatch",
        "compliance_violation",
        "duplicate_product",
        "identity_mismatch",
        "missing_attribute",
        "orphan_child",
        "relationship_break",
        "sku_conflict",
        "variant_conflict",
    ),
    "connection_status": ("active", "error", "expired", "pending", "revoked"),
    "execution_status": (
        "failed",
        "partially_failed",
        "pending",
        "rolled_back",
        "running",
        "succeeded",
    ),
    "product_status": ("active", "archived", "draft", "suppressed"),
    "proposal_status": (
        "approved",
        "executed",
        "executing",
        "failed",
        "pending_approval",
        "proposed",
        "rejected",
        "rejected_by_validation",
        "rolled_back",
        "simulated",
        "validated",
    ),
    "relationship_type": ("accessory", "bundle", "parent", "replacement", "variant"),
    "risk_level": ("CRITICAL", "HIGH", "LOW", "MEDIUM"),
    "source_type": (
        "ai_inferred",
        "erp",
        "historical",
        "internal",
        "manufacturer",
        "marketplace",
        "pim",
        "wms",
    ),
    "user_role": (
        "data_integration_admin",
        "data_steward",
        "ecommerce_ops_manager",
        "merchandiser",
        "seo_specialist",
    ),
}


def upgrade() -> None:
    # --------------------------------------------------------------------
    # agent_runs
    # Safe execution trace; excludes private chain-of-thought
    # --------------------------------------------------------------------
    op.create_table(
        "agent_runs",
        sa.Column("run_type", sa.String(length=64), nullable=False),
        sa.Column("graph_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("checkpoint_id", sa.String(length=255), nullable=True),
        sa.Column("input_summary", sa.JSON(), nullable=True),
        sa.Column("output_summary", sa.JSON(), nullable=True),
        sa.Column("node_trace", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("llm_calls", sa.Integer(), nullable=False),
        sa.Column("llm_tokens_used", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Double(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_agent_runs_workspace_id_id")),
        comment="Safe execution trace; excludes private chain-of-thought",
    )

    op.create_index("ix_agent_runs_deleted_at", "agent_runs", ["deleted_at"])
    op.create_index("ix_agent_runs_workspace_id", "agent_runs", ["workspace_id"])
    op.create_index("ix_agent_runs_workspace_started", "agent_runs", ["workspace_id", "started_at"])
    op.create_index("ix_agent_runs_workspace_status", "agent_runs", ["workspace_id", "status"])

    # --------------------------------------------------------------------
    # audit_logs
    # Append-only audit trail; UPDATE/DELETE revoked at the database
    # --------------------------------------------------------------------
    op.create_table(
        "audit_logs",
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_email", sa.String(length=320), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=128), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("old_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column(
            "risk_level",
            sa.Enum(
                "LOW", "MEDIUM", "HIGH", "CRITICAL", name="risk_level", native_enum=False, length=16
            ),
            nullable=True,
        ),
        sa.Column("approval_reference", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("agent_run_id", sa.String(length=64), nullable=True),
        sa.Column("source_reference", sa.String(length=255), nullable=True),
        sa.Column("execution_result", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_audit_logs_workspace_id_id")),
        comment="Append-only audit trail; UPDATE/DELETE revoked at the database",
    )

    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])
    op.create_index("ix_audit_logs_workspace_id", "audit_logs", ["workspace_id"])
    op.create_index("ix_audit_workspace_action", "audit_logs", ["workspace_id", "action"])
    op.create_index("ix_audit_workspace_created", "audit_logs", ["workspace_id", "created_at"])
    op.create_index(
        "ix_audit_workspace_entity", "audit_logs", ["workspace_id", "entity_type", "entity_id"]
    )

    # D-013: append-only enforced by the database, not by
    # application discipline. A trigger blocks UPDATE and DELETE
    # regardless of role.
    op.execute("""
        CREATE OR REPLACE FUNCTION catalogguard_audit_immutable()
        RETURNS TRIGGER AS $$
        BEGIN
        RAISE EXCEPTION 'audit_logs is append-only: % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER audit_logs_no_update
        BEFORE UPDATE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION catalogguard_audit_immutable();
    """)
    op.execute("""
        CREATE TRIGGER audit_logs_no_delete
        BEFORE DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION catalogguard_audit_immutable();
    """)
    # --------------------------------------------------------------------
    # catalog_sources
    # Ingested data source with freshness stamp
    # --------------------------------------------------------------------
    op.create_table(
        "catalog_sources",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum(
                "manufacturer",
                "pim",
                "erp",
                "wms",
                "internal",
                "marketplace",
                "historical",
                "ai_inferred",
                name="source_type",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("detected_schema", sa.JSON(), nullable=False),
        sa.Column("last_sequence_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authorization_context", sa.JSON(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_sources")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_catalog_sources_workspace_id_id")),
        comment="Ingested data source with freshness stamp",
    )

    op.create_index("ix_catalog_sources_deleted_at", "catalog_sources", ["deleted_at"])
    op.create_index("ix_catalog_sources_workspace_id", "catalog_sources", ["workspace_id"])
    op.create_index(
        "ix_catalog_sources_workspace_type", "catalog_sources", ["workspace_id", "source_type"]
    )

    # --------------------------------------------------------------------
    # catalogs
    # Tenant catalog scope; reconciliation runs reference a version
    # --------------------------------------------------------------------
    op.create_table(
        "catalogs",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalogs")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_catalogs_workspace_id_id")),
        sa.UniqueConstraint("workspace_id", "slug", name="uq_catalogs_workspace_slug"),
        comment="Tenant catalog scope; reconciliation runs reference a version",
    )

    op.create_index("ix_catalogs_deleted_at", "catalogs", ["deleted_at"])
    op.create_index("ix_catalogs_workspace_id", "catalogs", ["workspace_id"])

    # --------------------------------------------------------------------
    # compliance_documents
    # Compliance source document with filterable metadata
    # --------------------------------------------------------------------
    op.create_table(
        "compliance_documents",
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("effective_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("marketplace_code", sa.String(length=64), nullable=True),
        sa.Column("country_code", sa.String(length=8), nullable=True),
        sa.Column("category_path_text", sa.Text(), nullable=True),
        sa.Column("document_kind", sa.String(length=64), nullable=True),
        sa.Column("rule_kind", sa.String(length=64), nullable=True),
        sa.Column("version_label", sa.String(length=64), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compliance_documents")),
        sa.UniqueConstraint("workspace_id", "content_hash", name="uq_doc_content_hash"),
        sa.UniqueConstraint(
            "workspace_id", "id", name=op.f("uq_compliance_documents_workspace_id_id")
        ),
        comment="Compliance source document with filterable metadata",
    )

    op.create_index("ix_compliance_documents_deleted_at", "compliance_documents", ["deleted_at"])
    op.create_index(
        "ix_compliance_documents_workspace_id", "compliance_documents", ["workspace_id"]
    )
    op.create_index(
        "ix_docs_workspace_effective", "compliance_documents", ["workspace_id", "effective_date"]
    )
    op.create_index(
        "ix_docs_workspace_marketplace",
        "compliance_documents",
        ["workspace_id", "marketplace_code"],
    )

    # --------------------------------------------------------------------
    # email_outbox
    # Outbound mail awaiting a real transport
    # --------------------------------------------------------------------
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=320), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("template", sa.String(length=64), nullable=False),
        sa.Column("related_id", sa.UUID(), nullable=True),
        sa.Column("delivery_channel", sa.String(length=32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_outbox")),
        comment="Outbound mail awaiting a real transport",
    )

    op.create_index("ix_email_outbox_created_at", "email_outbox", ["created_at"])
    op.create_index("ix_email_outbox_recipient", "email_outbox", ["recipient"])
    op.create_index("ix_email_outbox_related_id", "email_outbox", ["related_id"])

    # --------------------------------------------------------------------
    # llm_usage
    # Per-call token and cost accounting
    # --------------------------------------------------------------------
    op.create_table(
        "llm_usage",
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("repair_attempts", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Double(), nullable=False),
        sa.Column("was_retry", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.CheckConstraint("estimated_cost_usd >= 0", name=op.f("ck_llm_usage_cost_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_usage")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_llm_usage_workspace_id_id")),
        comment="Per-call token and cost accounting",
    )

    op.create_index("ix_llm_usage_workspace_created", "llm_usage", ["workspace_id", "created_at"])
    op.create_index("ix_llm_usage_workspace_id", "llm_usage", ["workspace_id"])
    op.create_index("ix_llm_usage_workspace_model", "llm_usage", ["workspace_id", "model"])

    # --------------------------------------------------------------------
    # login_throttles
    # Failed-login counters for account and IP scopes
    # --------------------------------------------------------------------
    op.create_table(
        "login_throttles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_throttles")),
        sa.UniqueConstraint("scope", "key", name="uq_login_throttles_scope_key"),
        comment="Failed-login counters for account and IP scopes",
    )

    # --------------------------------------------------------------------
    # marketplace_connections
    # Marketplace credentials; encrypted at rest, never plaintext
    # --------------------------------------------------------------------
    op.create_table(
        "marketplace_connections",
        sa.Column("marketplace", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "active",
                "expired",
                "revoked",
                "error",
                name="connection_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("encrypted_credentials", sa.Text(), nullable=False),
        sa.Column("granted_scopes", sa.JSON(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketplace_connections")),
        sa.UniqueConstraint(
            "workspace_id", "id", name=op.f("uq_marketplace_connections_workspace_id_id")
        ),
        sa.UniqueConstraint(
            "workspace_id", "marketplace", "display_name", name="uq_connection_label"
        ),
        comment="Marketplace credentials; encrypted at rest, never plaintext",
    )

    op.create_index(
        "ix_connections_workspace_marketplace",
        "marketplace_connections",
        ["workspace_id", "marketplace"],
    )
    op.create_index(
        "ix_marketplace_connections_deleted_at", "marketplace_connections", ["deleted_at"]
    )
    op.create_index(
        "ix_marketplace_connections_workspace_id", "marketplace_connections", ["workspace_id"]
    )

    # --------------------------------------------------------------------
    # organizations
    # Top-level customer boundary
    # --------------------------------------------------------------------
    op.create_table(
        "organizations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
        sa.UniqueConstraint("slug", name=op.f("uq_organizations_slug")),
        comment="Top-level customer boundary",
    )

    op.create_index("ix_organizations_deleted_at", "organizations", ["deleted_at"])

    # --------------------------------------------------------------------
    # users
    # --------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )

    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])

    # --------------------------------------------------------------------
    # auth_sessions
    # Server-side login sessions; stores token digests only
    # --------------------------------------------------------------------
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_sessions_token_hash")),
        comment="Server-side login sessions; stores token digests only",
    )

    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    # --------------------------------------------------------------------
    # compliance_chunks
    # Retrievable chunk; semantic plus keyword plus metadata
    # --------------------------------------------------------------------
    op.create_table(
        "compliance_chunks",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("section_reference", sa.String(length=512), nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("search_vector", TSVECTOR(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "document_id"],
            ["compliance_documents.workspace_id", "compliance_documents.id"],
            name="fk_chunks_workspace_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compliance_chunks")),
        sa.UniqueConstraint("workspace_id", "document_id", "chunk_index", name="uq_chunk_index"),
        sa.UniqueConstraint(
            "workspace_id", "id", name=op.f("uq_compliance_chunks_workspace_id_id")
        ),
        comment="Retrievable chunk; semantic plus keyword plus metadata",
    )

    op.create_index(
        "ix_chunks_workspace_document", "compliance_chunks", ["workspace_id", "document_id"]
    )
    op.create_index("ix_compliance_chunks_deleted_at", "compliance_chunks", ["deleted_at"])
    op.create_index("ix_compliance_chunks_workspace_id", "compliance_chunks", ["workspace_id"])

    # --------------------------------------------------------------------
    # health_scores
    # Explainable health score with underlying issue counts
    # --------------------------------------------------------------------
    op.create_table(
        "health_scores",
        sa.Column("catalog_id", sa.Uuid(), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=False),
        sa.Column("data_quality", sa.Float(), nullable=False),
        sa.Column("consistency", sa.Float(), nullable=False),
        sa.Column("compliance", sa.Float(), nullable=False),
        sa.Column("relationship_integrity", sa.Float(), nullable=False),
        sa.Column("seo_readiness", sa.Float(), nullable=False),
        sa.Column("change_safety", sa.Float(), nullable=False),
        sa.Column("issue_counts", sa.JSON(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "catalog_id"],
            ["catalogs.workspace_id", "catalogs.id"],
            name="fk_health_workspace_catalog",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_health_scores")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_health_scores_workspace_id_id")),
        comment="Explainable health score with underlying issue counts",
    )

    op.create_index("ix_health_scores_deleted_at", "health_scores", ["deleted_at"])
    op.create_index("ix_health_scores_workspace_id", "health_scores", ["workspace_id"])
    op.create_index(
        "ix_health_workspace_catalog_time",
        "health_scores",
        ["workspace_id", "catalog_id", "computed_at"],
    )

    # --------------------------------------------------------------------
    # password_reset_tokens
    # Single-use password reset tokens; stores digests only
    # --------------------------------------------------------------------
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_ip", sa.String(length=45), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_password_reset_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_password_reset_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_password_reset_tokens_token_hash")),
        comment="Single-use password reset tokens; stores digests only",
    )

    op.create_index("ix_password_reset_tokens_expires_at", "password_reset_tokens", ["expires_at"])
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])

    # --------------------------------------------------------------------
    # products
    # Canonical product, current state only
    # --------------------------------------------------------------------
    op.create_table(
        "products",
        sa.Column("catalog_id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(length=255), nullable=False),
        sa.Column("mpn", sa.String(length=255), nullable=True),
        sa.Column("gtin", sa.String(length=64), nullable=True),
        sa.Column("upc", sa.String(length=64), nullable=True),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("manufacturer", sa.String(length=255), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category_path", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "draft",
                "archived",
                "suppressed",
                name="product_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("is_parent", sa.Boolean(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("media", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name=op.f("ck_products_not_self_parent")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "catalog_id"],
            ["catalogs.workspace_id", "catalogs.id"],
            name="fk_products_workspace_catalog",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "parent_id"],
            ["products.workspace_id", "products.id"],
            name="fk_products_workspace_parent",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_products_workspace_id_id")),
        sa.UniqueConstraint("workspace_id", "sku", name="uq_products_workspace_sku"),
        comment="Canonical product, current state only",
    )

    op.create_index("ix_products_deleted_at", "products", ["deleted_at"])
    op.create_index("ix_products_parent_id", "products", ["parent_id"])
    op.create_index("ix_products_workspace_gtin", "products", ["workspace_id", "gtin"])
    op.create_index("ix_products_workspace_id", "products", ["workspace_id"])
    op.create_index("ix_products_workspace_parent", "products", ["workspace_id", "parent_id"])
    op.create_index("ix_products_workspace_status", "products", ["workspace_id", "status"])

    # --------------------------------------------------------------------
    # reconciliation_runs
    # One reconciliation pass, bound to a catalog version
    # --------------------------------------------------------------------
    op.create_table(
        "reconciliation_runs",
        sa.Column("catalog_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("products_analyzed", sa.Integer(), nullable=False),
        sa.Column("conflicts_found", sa.Integer(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("llm_tokens_used", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "catalog_id"],
            ["catalogs.workspace_id", "catalogs.id"],
            name="fk_runs_workspace_catalog",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reconciliation_runs")),
        sa.UniqueConstraint(
            "workspace_id", "id", name=op.f("uq_reconciliation_runs_workspace_id_id")
        ),
        comment="One reconciliation pass, bound to a catalog version",
    )

    op.create_index("ix_reconciliation_runs_deleted_at", "reconciliation_runs", ["deleted_at"])
    op.create_index("ix_reconciliation_runs_workspace_id", "reconciliation_runs", ["workspace_id"])
    op.create_index(
        "ix_runs_workspace_catalog_time",
        "reconciliation_runs",
        ["workspace_id", "catalog_id", "started_at"],
    )

    # --------------------------------------------------------------------
    # source_records
    # Raw ingested record, pre-normalization
    # --------------------------------------------------------------------
    op.create_table(
        "source_records",
        sa.Column("catalog_source_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.Column("sequence_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "catalog_source_id"],
            ["catalog_sources.workspace_id", "catalog_sources.id"],
            name="fk_source_records_workspace_source",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_records")),
        sa.UniqueConstraint(
            "workspace_id", "catalog_source_id", "external_id", name="uq_source_record"
        ),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_source_records_workspace_id_id")),
        comment="Raw ingested record, pre-normalization",
    )

    op.create_index("ix_source_records_deleted_at", "source_records", ["deleted_at"])
    op.create_index("ix_source_records_workspace_id", "source_records", ["workspace_id"])
    op.create_index(
        "ix_source_records_workspace_source",
        "source_records",
        ["workspace_id", "catalog_source_id"],
    )

    # --------------------------------------------------------------------
    # tool_calls
    # Tool invocation record; arguments masked at the edge
    # --------------------------------------------------------------------
    op.create_table(
        "tool_calls",
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=True),
        sa.Column("result_summary", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "agent_run_id"],
            ["agent_runs.workspace_id", "agent_runs.id"],
            name="fk_tool_calls_workspace_run",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_calls")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_tool_calls_workspace_id_id")),
        comment="Tool invocation record; arguments masked at the edge",
    )

    op.create_index("ix_tool_calls_workspace_id", "tool_calls", ["workspace_id"])
    op.create_index("ix_tool_calls_workspace_name", "tool_calls", ["workspace_id", "tool_name"])
    op.create_index("ix_tool_calls_workspace_run", "tool_calls", ["workspace_id", "agent_run_id"])

    # --------------------------------------------------------------------
    # workspaces
    # Primary tenant boundary for all catalog data
    # --------------------------------------------------------------------
    op.create_table(
        "workspaces",
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("source_authority_config", sa.Text(), nullable=True),
        sa.Column("risk_config", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_workspaces_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspaces")),
        comment="Primary tenant boundary for all catalog data",
    )

    op.create_index("ix_workspaces_deleted_at", "workspaces", ["deleted_at"])
    op.create_index("ix_workspaces_organization_id", "workspaces", ["organization_id"])
    op.create_index(
        "uq_workspaces_org_slug", "workspaces", ["organization_id", "slug"], unique=True
    )

    # --------------------------------------------------------------------
    # change_proposals
    # Validated, risk-scored change request awaiting approval
    # --------------------------------------------------------------------
    op.create_table(
        "change_proposals",
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("reconciliation_run_id", sa.Uuid(), nullable=True),
        sa.Column("change_ref", sa.String(length=64), nullable=False),
        sa.Column("field_name", sa.String(length=255), nullable=False),
        sa.Column("current_value", sa.JSON(), nullable=True),
        sa.Column("proposed_value", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("validation_status", sa.String(length=32), nullable=False),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column(
            "risk_level",
            sa.Enum(
                "LOW", "MEDIUM", "HIGH", "CRITICAL", name="risk_level", native_enum=False, length=16
            ),
            nullable=False,
        ),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("seo_impact", sa.String(length=32), nullable=False),
        sa.Column("compliance_impact", sa.String(length=32), nullable=False),
        sa.Column("affected_channels", sa.JSON(), nullable=False),
        sa.Column("affected_relationships", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "proposed",
                "validated",
                "rejected_by_validation",
                "simulated",
                "pending_approval",
                "approved",
                "rejected",
                "executing",
                "executed",
                "failed",
                "rolled_back",
                name="proposal_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "confidence > 0.0 AND confidence < 1.0",
            name=op.f("ck_change_proposals_confidence_range"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_proposals_workspace_product",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "reconciliation_run_id"],
            ["reconciliation_runs.workspace_id", "reconciliation_runs.id"],
            name="fk_proposals_workspace_run",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_proposals")),
        sa.UniqueConstraint("workspace_id", "change_ref", name="uq_proposals_change_ref"),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_change_proposals_workspace_id_id")),
        comment="Validated, risk-scored change request awaiting approval",
    )

    op.create_index("ix_change_proposals_deleted_at", "change_proposals", ["deleted_at"])
    op.create_index("ix_change_proposals_workspace_id", "change_proposals", ["workspace_id"])
    op.create_index(
        "ix_proposals_workspace_risk", "change_proposals", ["workspace_id", "risk_level"]
    )
    op.create_index("ix_proposals_workspace_status", "change_proposals", ["workspace_id", "status"])

    # --------------------------------------------------------------------
    # conflicts
    # Detected disagreement; all competing values retained
    # --------------------------------------------------------------------
    op.create_table(
        "conflicts",
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("reconciliation_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "conflict_type",
            sa.Enum(
                "attribute_mismatch",
                "missing_attribute",
                "duplicate_product",
                "sku_conflict",
                "variant_conflict",
                "relationship_break",
                "orphan_child",
                "compliance_violation",
                "identity_mismatch",
                name="conflict_type",
                native_enum=False,
                length=64,
            ),
            nullable=False,
        ),
        sa.Column("field_name", sa.String(length=255), nullable=False),
        sa.Column("competing_values", sa.JSON(), nullable=False),
        sa.Column("resolved_value", sa.JSON(), nullable=True),
        sa.Column("resolved_by", sa.String(length=255), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_conflicts_workspace_product",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "reconciliation_run_id"],
            ["reconciliation_runs.workspace_id", "reconciliation_runs.id"],
            name="fk_conflicts_workspace_run",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conflicts")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_conflicts_workspace_id_id")),
        comment="Detected disagreement; all competing values retained",
    )

    op.create_index("ix_conflicts_deleted_at", "conflicts", ["deleted_at"])
    op.create_index("ix_conflicts_workspace_field", "conflicts", ["workspace_id", "field_name"])
    op.create_index("ix_conflicts_workspace_id", "conflicts", ["workspace_id"])
    op.create_index("ix_conflicts_workspace_product", "conflicts", ["workspace_id", "product_id"])
    op.create_index("ix_conflicts_workspace_type", "conflicts", ["workspace_id", "conflict_type"])

    # --------------------------------------------------------------------
    # marketplace_products
    # Channel listing, separate from canonical product
    # --------------------------------------------------------------------
    op.create_table(
        "marketplace_products",
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("marketplace", sa.String(length=64), nullable=False),
        sa.Column("external_product_id", sa.String(length=255), nullable=False),
        sa.Column("external_sku", sa.String(length=255), nullable=True),
        sa.Column("listing_status", sa.String(length=64), nullable=True),
        sa.Column("raw_listing", sa.JSON(), nullable=False),
        sa.Column("listing_url", sa.Text(), nullable=True),
        sa.Column("handle", sa.String(length=255), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_mkt_products_workspace_product",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_marketplace_products")),
        sa.UniqueConstraint(
            "workspace_id", "id", name=op.f("uq_marketplace_products_workspace_id_id")
        ),
        sa.UniqueConstraint(
            "workspace_id", "marketplace", "external_product_id", name="uq_marketplace_product"
        ),
        comment="Channel listing, separate from canonical product",
    )

    op.create_index("ix_marketplace_products_deleted_at", "marketplace_products", ["deleted_at"])
    op.create_index(
        "ix_marketplace_products_workspace_id", "marketplace_products", ["workspace_id"]
    )
    op.create_index(
        "ix_mkt_products_workspace_marketplace",
        "marketplace_products",
        ["workspace_id", "marketplace"],
    )
    op.create_index(
        "ix_mkt_products_workspace_product", "marketplace_products", ["workspace_id", "product_id"]
    )

    # --------------------------------------------------------------------
    # product_identifiers
    # External identifiers for deterministic identity resolution
    # --------------------------------------------------------------------
    op.create_table(
        "product_identifiers",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("identifier_type", sa.String(length=32), nullable=False),
        sa.Column("identifier_value", sa.String(length=255), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum(
                "manufacturer",
                "pim",
                "erp",
                "wms",
                "internal",
                "marketplace",
                "historical",
                "ai_inferred",
                name="source_type",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("source_record_id", sa.Uuid(), nullable=True),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_identifiers_workspace_product",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_identifiers")),
        sa.UniqueConstraint(
            "workspace_id", "id", name=op.f("uq_product_identifiers_workspace_id_id")
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "identifier_type",
            "identifier_value",
            "source_record_id",
            name="uq_identifiers_scope",
        ),
        comment="External identifiers for deterministic identity resolution",
    )

    op.create_index(
        "ix_identifiers_lookup",
        "product_identifiers",
        ["workspace_id", "identifier_type", "identifier_value"],
    )
    op.create_index("ix_product_identifiers_deleted_at", "product_identifiers", ["deleted_at"])
    op.create_index("ix_product_identifiers_workspace_id", "product_identifiers", ["workspace_id"])

    # --------------------------------------------------------------------
    # product_relationships
    # Product family structure; always approval-gated
    # --------------------------------------------------------------------
    op.create_table(
        "product_relationships",
        sa.Column("parent_id", sa.Uuid(), nullable=False),
        sa.Column("child_id", sa.Uuid(), nullable=False),
        sa.Column(
            "relationship_type",
            sa.Enum(
                "parent",
                "variant",
                "bundle",
                "replacement",
                "accessory",
                name="relationship_type",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("established_by", sa.String(length=255), nullable=False),
        sa.Column("approval_reference", sa.Uuid(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("parent_id <> child_id", name=op.f("ck_product_relationships_not_self")),
        sa.ForeignKeyConstraint(
            ["workspace_id", "child_id"],
            ["products.workspace_id", "products.id"],
            name="fk_rel_workspace_child",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "parent_id"],
            ["products.workspace_id", "products.id"],
            name="fk_rel_workspace_parent",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_relationships")),
        sa.UniqueConstraint(
            "workspace_id", "id", name=op.f("uq_product_relationships_workspace_id_id")
        ),
        sa.UniqueConstraint("workspace_id", "parent_id", "child_id", name="uq_relationship_pair"),
        comment="Product family structure; always approval-gated",
    )

    op.create_index("ix_product_relationships_deleted_at", "product_relationships", ["deleted_at"])
    op.create_index(
        "ix_product_relationships_workspace_id", "product_relationships", ["workspace_id"]
    )
    op.create_index(
        "ix_relationships_workspace_child", "product_relationships", ["workspace_id", "child_id"]
    )
    op.create_index(
        "ix_relationships_workspace_parent", "product_relationships", ["workspace_id", "parent_id"]
    )

    # --------------------------------------------------------------------
    # product_versions
    # Immutable product history for audit and replay
    # --------------------------------------------------------------------
    op.create_table(
        "product_versions",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("reconciliation_run_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_product_versions_workspace_product",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_versions")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_product_versions_workspace_id_id")),
        sa.UniqueConstraint(
            "workspace_id", "product_id", "version", name="uq_product_versions_number"
        ),
        comment="Immutable product history for audit and replay",
    )

    op.create_index("ix_product_versions_workspace_id", "product_versions", ["workspace_id"])

    # --------------------------------------------------------------------
    # workspace_invitations
    # Single-use workspace invitations with a bound role
    # --------------------------------------------------------------------
    op.create_table(
        "workspace_invitations",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "ecommerce_ops_manager",
                "data_steward",
                "data_integration_admin",
                "merchandiser",
                "seo_specialist",
                name="user_role",
                native_enum=False,
                length=64,
            ),
            nullable=False,
        ),
        sa.Column("invited_by_user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["users.id"],
            name=op.f("fk_workspace_invitations_accepted_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["users.id"],
            name=op.f("fk_workspace_invitations_invited_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_invitations_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_invitations")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_workspace_invitations_token_hash")),
        sa.UniqueConstraint(
            "workspace_id", "id", name=op.f("uq_workspace_invitations_workspace_id_id")
        ),
        comment="Single-use workspace invitations with a bound role",
    )

    op.create_index("ix_workspace_invitations_email", "workspace_invitations", ["email"])
    op.create_index(
        "ix_workspace_invitations_workspace_id", "workspace_invitations", ["workspace_id"]
    )
    op.create_index(
        "uq_workspace_invitations_live",
        "workspace_invitations",
        ["workspace_id", "email"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL AND accepted_at IS NULL"),
    )

    # --------------------------------------------------------------------
    # workspace_memberships
    # Per-workspace role assignment
    # --------------------------------------------------------------------
    op.create_table(
        "workspace_memberships",
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "ecommerce_ops_manager",
                "data_steward",
                "data_integration_admin",
                "merchandiser",
                "seo_specialist",
                name="user_role",
                native_enum=False,
                length=64,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_workspace_memberships_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_workspace_memberships_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workspace_memberships")),
        sa.UniqueConstraint(
            "workspace_id", "id", name=op.f("uq_workspace_memberships_workspace_id_id")
        ),
        comment="Per-workspace role assignment",
    )

    op.create_index(
        "uq_memberships_workspace_user",
        "workspace_memberships",
        ["workspace_id", "user_id"],
        unique=True,
    )

    # --------------------------------------------------------------------
    # approval_requests
    # Human approval gate; role-gated per D-003
    # --------------------------------------------------------------------
    op.create_table(
        "approval_requests",
        sa.Column("change_proposal_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("required_approvals", sa.Integer(), nullable=False),
        sa.Column("approvals_received", sa.Integer(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "approvals_received <= required_approvals",
            name=op.f("ck_approval_requests_not_exceeded"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "change_proposal_id"],
            ["change_proposals.workspace_id", "change_proposals.id"],
            name="fk_approvals_workspace_proposal",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests")),
        sa.UniqueConstraint(
            "workspace_id", "id", name=op.f("uq_approval_requests_workspace_id_id")
        ),
        comment="Human approval gate; role-gated per D-003",
    )

    op.create_index("ix_approval_requests_deleted_at", "approval_requests", ["deleted_at"])
    op.create_index("ix_approval_requests_workspace_id", "approval_requests", ["workspace_id"])
    op.create_index(
        "ix_approvals_workspace_status", "approval_requests", ["workspace_id", "status"]
    )

    # --------------------------------------------------------------------
    # execution_jobs
    # Execution against a marketplace, with bounded retries
    # --------------------------------------------------------------------
    op.create_table(
        "execution_jobs",
        sa.Column("change_proposal_id", sa.Uuid(), nullable=False),
        sa.Column("approval_request_id", sa.Uuid(), nullable=True),
        sa.Column("connector", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "succeeded",
                "failed",
                "partially_failed",
                "rolled_back",
                name="execution_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("previous_state", sa.JSON(), nullable=True),
        sa.Column("rollback_supported", sa.Boolean(), nullable=False),
        sa.Column("rollback_reference", sa.String(length=255), nullable=True),
        sa.Column("marketplace_response", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("recovery_instructions", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_count <= max_attempts", name=op.f("ck_execution_jobs_attempts_bounded")
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "change_proposal_id"],
            ["change_proposals.workspace_id", "change_proposals.id"],
            name="fk_execution_workspace_proposal",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_jobs")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_execution_jobs_workspace_id_id")),
        comment="Execution against a marketplace, with bounded retries",
    )

    op.create_index("ix_execution_jobs_deleted_at", "execution_jobs", ["deleted_at"])
    op.create_index("ix_execution_jobs_workspace_id", "execution_jobs", ["workspace_id"])
    op.create_index("ix_execution_workspace_status", "execution_jobs", ["workspace_id", "status"])

    # --------------------------------------------------------------------
    # risk_assessments
    # Deterministic risk scoring with factor breakdown
    # --------------------------------------------------------------------
    op.create_table(
        "risk_assessments",
        sa.Column("change_proposal_id", sa.Uuid(), nullable=False),
        sa.Column(
            "risk_level",
            sa.Enum(
                "LOW", "MEDIUM", "HIGH", "CRITICAL", name="risk_level", native_enum=False, length=16
            ),
            nullable=False,
        ),
        sa.Column("numeric_score", sa.Float(), nullable=False),
        sa.Column("factors", sa.JSON(), nullable=False),
        sa.Column("escalated_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "numeric_score >= 0.0 AND numeric_score <= 100.0",
            name=op.f("ck_risk_assessments_score_range"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "change_proposal_id"],
            ["change_proposals.workspace_id", "change_proposals.id"],
            name="fk_risk_workspace_proposal",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_risk_assessments")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_risk_assessments_workspace_id_id")),
        comment="Deterministic risk scoring with factor breakdown",
    )

    op.create_index("ix_risk_assessments_deleted_at", "risk_assessments", ["deleted_at"])
    op.create_index("ix_risk_assessments_workspace_id", "risk_assessments", ["workspace_id"])
    op.create_index(
        "ix_risk_workspace_proposal", "risk_assessments", ["workspace_id", "change_proposal_id"]
    )

    # --------------------------------------------------------------------
    # simulation_runs
    # Sandbox simulation; never modifies production
    # --------------------------------------------------------------------
    op.create_table(
        "simulation_runs",
        sa.Column("change_proposal_id", sa.Uuid(), nullable=False),
        sa.Column("base_catalog_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("schema_valid", sa.Boolean(), nullable=True),
        sa.Column("relationship_valid", sa.Boolean(), nullable=True),
        sa.Column("compliance_valid", sa.Boolean(), nullable=True),
        sa.Column("seo_sensitive", sa.Boolean(), nullable=True),
        sa.Column(
            "overall_risk",
            sa.Enum(
                "LOW", "MEDIUM", "HIGH", "CRITICAL", name="risk_level", native_enum=False, length=16
            ),
            nullable=True,
        ),
        sa.Column("diff", sa.JSON(), nullable=False),
        sa.Column("impact_report", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "change_proposal_id"],
            ["change_proposals.workspace_id", "change_proposals.id"],
            name="fk_simulations_workspace_proposal",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_simulation_runs")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_simulation_runs_workspace_id_id")),
        comment="Sandbox simulation; never modifies production",
    )

    op.create_index("ix_simulation_runs_deleted_at", "simulation_runs", ["deleted_at"])
    op.create_index("ix_simulation_runs_workspace_id", "simulation_runs", ["workspace_id"])
    op.create_index(
        "ix_simulations_workspace_proposal",
        "simulation_runs",
        ["workspace_id", "change_proposal_id"],
    )

    # --------------------------------------------------------------------
    # approval_actions
    # Individual operator decision on a change
    # --------------------------------------------------------------------
    op.create_table(
        "approval_actions",
        sa.Column("approval_request_id", sa.Uuid(), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=False),
        sa.Column(
            "decision",
            sa.Enum(
                "approved",
                "rejected",
                "modified",
                "re_analysis_requested",
                name="approval_decision",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("modified_value", sa.JSON(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "approval_request_id"],
            ["approval_requests.workspace_id", "approval_requests.id"],
            name="fk_approval_actions_workspace_request",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_actions")),
        sa.UniqueConstraint("workspace_id", "id", name=op.f("uq_approval_actions_workspace_id_id")),
        comment="Individual operator decision on a change",
    )

    op.create_index("ix_approval_actions_deleted_at", "approval_actions", ["deleted_at"])
    op.create_index("ix_approval_actions_workspace_id", "approval_actions", ["workspace_id"])
    op.create_index(
        "ix_approval_actions_workspace_request",
        "approval_actions",
        ["workspace_id", "approval_request_id"],
    )

    # D-011: RLS goes on last, once every table exists, so the policy
    # list is derived from the metadata rather than copied by hand.
    _enable_rls()


def _enable_rls() -> None:
    """Attach a tenant policy to every tenant-owned table.

    Tables in `GLOBAL_TABLES` sit above or at the tenant boundary and are
    skipped deliberately. This runs after every table exists, so the policy
    list comes from the metadata rather than a hand-maintained copy of it.
    """
    for table in Base.metadata.sorted_tables:
        if table.name in GLOBAL_TABLES or "workspace_id" not in table.c:
            continue
        op.execute(enable_rls_sql(table.name))
        op.execute(create_policy_sql(table.name))


def downgrade() -> None:
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in GLOBAL_TABLES or "workspace_id" not in table.c:
            continue
        op.execute(drop_policy_sql(table.name))
    # Reverse creation order so children drop before parents.
    for table in (
        "approval_actions",
        "simulation_runs",
        "risk_assessments",
        "execution_jobs",
        "approval_requests",
        "workspace_memberships",
        "workspace_invitations",
        "product_versions",
        "product_relationships",
        "product_identifiers",
        "marketplace_products",
        "conflicts",
        "change_proposals",
        "workspaces",
        "tool_calls",
        "source_records",
        "reconciliation_runs",
        "products",
        "password_reset_tokens",
        "health_scores",
        "compliance_chunks",
        "auth_sessions",
        "users",
        "organizations",
        "marketplace_connections",
        "login_throttles",
        "llm_usage",
        "email_outbox",
        "compliance_documents",
        "catalogs",
        "catalog_sources",
        "audit_logs",
        "agent_runs",
    ):
        op.drop_table(table)

    # The triggers are dropped with the table; the function is not.
    op.execute("DROP FUNCTION IF EXISTS catalogguard_audit_immutable()")
