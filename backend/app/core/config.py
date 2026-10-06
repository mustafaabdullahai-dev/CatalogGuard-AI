"""Application configuration.

Single source of truth for settings. Loaded from environment/.env.
Fails fast on missing secrets: a silent default is a security incident.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Repo-root .env, since backend/ is the package root and the app is
        # started from there.
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_name: str = "CatalogGuard AI"
    environment: Literal["development", "test", "staging", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    # Database
    database_url: str = Field(..., description="Postgres connection string")
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_echo: bool = False
    enable_pgvector: bool = True

    # Redis / queue
    redis_url: str = Field(..., description="Redis connection string for Dramatiq")
    dramatiq_max_retries: int = 3
    dramatiq_concurrency: int = 4
    llm_max_concurrency: int = 2

    # LLM
    qroq_api_key: str = Field(..., description="Qroq API key. Never log this.")
    qroq_base_url: str = "https://api.groq.com/openai/v1"
    # Both model ids below were confirmed against this provider's /models and
    # /chat/completions on 2026-10-06, schema-constrained JSON included. The
    # previous fast-model default (`llama-3.3-70b-versatile`) was not in the
    # list the provider returned: a default naming a model that does not exist
    # fails at the first call, far from the setting that caused it.
    qroq_fast_model: str = "openai/gpt-oss-20b"
    qroq_reasoning_model: str = "openai/gpt-oss-120b"
    # Empty on purpose. This provider's /embeddings answered `model_not_found`
    # on 2026-10-06 for every candidate id, so no value here would work — an
    # empty string with a startup check says so once, instead of failing an
    # ingest later and blaming the document.
    qroq_embedding_model: str = ""
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2
    llm_schema_repair_attempts: int = 2
    # A model that keeps asking for tools and never answers is as unbounded as a
    # model that keeps failing validation: both spend tokens forever.
    llm_max_tool_rounds: int = 3
    # Ceiling on one tool's serialized output, so no single result can fill the
    # context window. Tools bound their own lists too; this is the backstop.
    tool_output_max_chars: int = 8000
    llm_temperature: float = 0.0

    # Security
    jwt_secret: str = Field(..., min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30
    encryption_key: str = Field(..., description="Fernet key for credential encryption")

    # Authentication (D-014)
    # Session cookies, not JWTs. The session lives in Postgres so it can be
    # revoked, which matters when approval authority is the thing being
    # protected. The JWT settings above remain for future machine-to-machine
    # tokens between the API and workers.
    session_cookie_name: str = "cg_session"
    csrf_cookie_name: str = "cg_csrf"
    csrf_header_name: str = "X-CSRF-Token"
    session_idle_timeout_minutes: int = 720
    session_absolute_timeout_hours: int = 336
    session_cookie_secure: bool = True
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # Argon2id. Defaults follow the OWASP password-storage recommendation
    # (64 MiB, 3 passes, 4 lanes) and are recorded in every hash, so raising
    # them later rehashes on each user's next successful login instead of
    # invalidating anyone's password.
    argon2_time_cost: int = 3
    argon2_memory_cost_kib: int = 65536
    argon2_parallelism: int = 4
    argon2_hash_len: int = 32
    argon2_salt_len: int = 16

    # Login throttling. Two independent counters so neither a targeted attempt
    # on one account nor a spray across many accounts gets a free run.
    login_max_failures: int = 5
    login_window_minutes: int = 15
    login_lockout_minutes: int = 15
    login_ip_max_failures: int = 20

    # Password reset and invitations (D-017).
    #
    # Deliberately separate budgets from the login counters. Sharing them would
    # let anyone who can submit a reset request for a victim's address lock that
    # account out of signing in, which turns a convenience feature into a
    # denial-of-service lever aimed at the very person using it.
    reset_max_requests: int = 3
    reset_window_minutes: int = 60
    reset_lockout_minutes: int = 15
    reset_ip_max_requests: int = 20
    # Short. A forgotten password should not need an operator, but a token
    # valid for a week in a shared inbox is a credential waiting to be found.
    reset_token_ttl_minutes: int = 60
    # Cap on live tokens per account. Older ones are invalidated when a new one
    # is issued, so this is also the blast radius of a leaked inbox history.
    reset_max_live_tokens: int = 3
    invitation_token_ttl_hours: int = 72
    invitation_max_per_recipient: int = 10

    # Outbound mail. `outbox` writes the message to `email_outbox` and stops
    # there, which is honest: the row says it was never sent rather than
    # implying a transport exists. Adding a provider means adding an adapter
    # and a value here; no caller changes.
    mail_delivery: Literal["outbox"] = "outbox"
    # Where the link in a message points. Required, because a reset link built
    # from the API's own host would send people to an internal hostname.
    app_base_url: str = "http://localhost:3000"

    # Passwords
    password_min_length: int = 12
    # NoDecode: pydantic-settings JSON-decodes complex fields from .env by
    # default, which rejects plain comma-separated values. Disabling that
    # lets the _split_csv validator below handle the raw string.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    log_mask_keys: Annotated[list[str], NoDecode] = [
        "sku",
        "price",
        "cost",
        "supplier_cost",
        "margin",
        "unreleased_description",
        "upc",
        "ean",
        "gtin",
        "asin",
    ]

    # Observability
    langfuse_enabled: bool = False
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Uploads
    upload_dir: str = "./uploads"
    max_upload_mb: int = 100

    # Catalog ingestion (Phase 10, §49).
    #
    # CSV, JSON and Excel are parsed in process. XML and PDF are deliberately
    # absent until a parser is chosen: a parser is the most dangerous component
    # in an intake path, and `defusedxml` / a PDF extractor are not installed.
    # Accepting a format nothing can read would create a source with zero rows
    # and no explanation; refusing the extension is the honest behaviour.
    ingestion_allowed_extensions: Annotated[list[str], NoDecode] = [
        ".csv",
        ".json",
        ".xlsx",
    ]
    # Files at or below this are parsed inline; larger ones are handed to the
    # Dramatiq worker (§28) -- but only when INGESTION_EXECUTION is `dramatiq`.
    ingestion_inline_max_bytes: int = 5 * 1024 * 1024
    # `inline` keeps the whole pipeline inside the request, which is honest for
    # the current deployment: no worker is running. `dramatiq` enqueues oversized
    # files and requires a live broker.
    ingestion_execution: Literal["inline", "dramatiq"] = "inline"
    # Structural caps. Rows truncate with a warning; columns abort, because a
    # file with more columns than this is a different file than the one expected.
    ingestion_max_rows: int = 200_000
    ingestion_max_columns: int = 512
    # §49 lists a security check. No anti-malware engine is deployed, so the
    # default reports `none` rather than implying a scan happened.
    ingestion_scanner: Literal["none"] = "none"
    # A CSV cell beginning with `=`, `+`, `-` or `@` can execute as a formula when
    # a reviewer opens an export. Neutralised unless it is a bare signed number.
    ingestion_neutralize_formulas: bool = True

    # Compliance RAG (Phases 13–14, §13/§14, D-006).
    #
    # `none` stores chunks WITHOUT vectors, so retrieval degrades to keyword and
    # metadata evidence instead of returning fabricated similarity. `qroq` needs a
    # live key and pgvector on Neon -- neither is confirmed -- so `none` is the
    # honest default: un-indexed is knowable, fake-indexed is not.
    rag_embedding_mode: Literal["none", "qroq"] = "none"
    rag_chunk_max_chars: int = 1200
    rag_chunk_overlap: int = 150
    # Candidates fetched per signal before reranking. 50 keeps each pass a single
    # indexed lookup while leaving enough room for a weak keyword pass to recover.
    rag_retrieval_candidates: int = 50
    rag_retrieval_results: int = 8

    # Cost control
    llm_cost_alert_enabled: bool = True
    llm_monthly_budget_usd: float = 0.0

    @field_validator(
        "cors_origins", "log_mask_keys", "ingestion_allowed_extensions", mode="before"
    )
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Accept comma-separated strings from env/.env.

        pydantic-settings attempts JSON decoding for complex fields before
        validators run, so a bare comma-separated value fails to parse.
        Returning a JSON list here satisfies that decoder while still
        handling an already-decoded list.
        """
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("jwt_secret")
    @classmethod
    def _reject_placeholder_secret(cls, v: str) -> str:
        if "REPLACE_ME" in v:
            raise ValueError("JWT_SECRET is still a placeholder. Generate a real secret.")
        return v

    @field_validator("encryption_key")
    @classmethod
    def _validate_fernet_key(cls, v: str) -> str:
        if "REPLACE_ME" in v:
            raise ValueError("ENCRYPTION_KEY is still a placeholder.")
        try:
            from cryptography.fernet import Fernet

            Fernet(v)
        except Exception as exc:
            raise ValueError(
                "ENCRYPTION_KEY must be a valid Fernet key. "
                "Generate with: python -c \"from cryptography.fernet import "
                'Fernet; print(Fernet.generate_key().decode())"'
            ) from exc
        return v

    @field_validator("qroq_api_key")
    @classmethod
    def _reject_placeholder_llm_key(cls, v: str) -> str:
        if "REPLACE_ME" in v:
            raise ValueError("QROQ_API_KEY is still a placeholder.")
        return v

    @model_validator(mode="after")
    def _embedding_mode_needs_a_model(self) -> Settings:
        """Refuse a semantic-indexing mode that cannot produce vectors.

        Without this, `RAG_EMBEDDING_MODE=qroq` loads fine and fails at the
        first ingest, where the error reads like a bad document — and by then
        the deployment believes it indexes semantically.
        """
        if self.rag_embedding_mode == "qroq" and not self.qroq_embedding_model:
            raise ValueError(
                "RAG_EMBEDDING_MODE=qroq requires QROQ_EMBEDDING_MODEL. This "
                "provider served no embedding model when probed, so the value is "
                "empty by default: set RAG_EMBEDDING_MODE=none to index on "
                "keyword and metadata evidence only, or point at a provider "
                "that embeds."
            )
        return self

    @field_validator("redis_url")
    @classmethod
    def _require_redis_tls(cls, v: str) -> str:
        """Upstash mandates TLS.

        A `redis://` scheme fails when the connection is first used, deep in
        a worker process, rather than at startup. Reject it during config
        load so the failure is immediate and legible.
        """
        if v.startswith("redis://"):
            raise ValueError(
                "REDIS_URL must use the rediss:// scheme. Upstash requires TLS; "
                "a plaintext redis:// connection fails only when first used."
            )
        if not v.startswith("rediss://"):
            raise ValueError("REDIS_URL must start with rediss:// (TLS is mandatory).")
        return v

    @field_validator("database_url")
    @classmethod
    def _require_postgres_tls(cls, v: str) -> str:
        """Neon mandates TLS. sslmode is required in the connection string."""
        if not v.startswith("postgresql://") and not v.startswith("postgres://"):
            raise ValueError("DATABASE_URL must be a postgres:// or postgresql:// URL.")
        if "sslmode=" not in v:
            raise ValueError(
                "DATABASE_URL must set sslmode (use 'sslmode=require'). "
                "Neon requires TLS."
            )
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cookie_secure(self) -> bool:
        """`Secure` on the session cookie, always in production.

        Configuration can relax this for local HTTP development; it must not be
        relaxable in production, where a session cookie sent over plaintext is
        a stolen session.
        """
        return self.session_cookie_secure or self.is_production

    @property
    def mask_key_set(self) -> frozenset[str]:
        return frozenset(self.log_mask_keys)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_settings_or_none() -> Settings | None:
    """Non-raising accessor for import-time and CLI contexts.

    Module-level `settings` is intentionally absent: importing this module
    must not explode when a placeholder secret is still present, because
    that breaks test collection and any CLI tooling that only needs the
    field list. Startup paths call `get_settings()` and surface the error.
    """
    try:
        return get_settings()
    except ValidationError:
        return None