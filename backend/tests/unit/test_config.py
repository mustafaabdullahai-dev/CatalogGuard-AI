"""Configuration guard tests.

These enforce the security invariants in D-006 and D-007. A regression here
means either a secret can ship to a third party or a plaintext connection
fails deep inside a worker instead of at startup.
"""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import Settings, async_database_url

# Generated test-only key. Never used outside the test suite.
TEST_FERNET_KEY = Fernet.generate_key().decode()

VALID: dict[str, Any] = {
    "database_url": "postgresql://u:p@host.neon.tech:5432/db?sslmode=require",
    "redis_url": "rediss://default:pw@host.upstash.io:6379/0",
    "qroq_api_key": "test-key",
    "jwt_secret": "x" * 48,
    "encryption_key": TEST_FERNET_KEY,
}


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **{**VALID, **overrides})


class TestHappyPath:
    def test_loads_with_valid_config(self) -> None:
        s = _settings()
        assert s.environment == "development"
        assert not s.is_production


class TestTlsEnforcement:
    def test_rejects_plaintext_redis(self) -> None:
        """A redis:// URL fails at first use, not at load. Must fail fast."""
        with pytest.raises(ValidationError, match="rediss"):
            _settings(redis_url="redis://default:pw@host.upstash.io:6379/0")

    def test_rejects_unknown_redis_scheme(self) -> None:
        with pytest.raises(ValidationError, match="rediss"):
            _settings(redis_url="http://host:6379")

    def test_rejects_postgres_without_sslmode(self) -> None:
        with pytest.raises(ValidationError, match="sslmode"):
            _settings(database_url="postgresql://u:p@host:5432/db")

    def test_accepts_postgres_with_sslmode(self) -> None:
        assert _settings().database_url.endswith("sslmode=require")


class TestPlaceholderGuards:
    @pytest.mark.parametrize("field", ["qroq_api_key", "jwt_secret", "encryption_key"])
    def test_rejects_placeholder_secrets(self, field: str) -> None:
        with pytest.raises(ValidationError):
            _settings(**{field: "REPLACE_ME"})

    def test_rejects_weak_jwt_secret(self) -> None:
        with pytest.raises(ValidationError):
            _settings(jwt_secret="tooshort")

    def test_rejects_invalid_fernet_key(self) -> None:
        with pytest.raises(ValidationError, match="Fernet"):
            _settings(encryption_key="not-a-fernet-key")


class TestListParsing:
    def test_parses_comma_separated_cors(self) -> None:
        s = _settings(cors_origins="http://a.com, http://b.com")
        assert s.cors_origins == ["http://a.com", "http://b.com"]

    def test_parses_comma_separated_mask_keys(self) -> None:
        s = _settings(log_mask_keys="sku, price ,margin")
        assert s.log_mask_keys == ["sku", "price", "margin"]

    def test_ignores_empty_segments(self) -> None:
        assert _settings(log_mask_keys="sku,,price,").log_mask_keys == ["sku", "price"]

    def test_mask_key_set_is_frozenset(self) -> None:
        assert isinstance(_settings().mask_key_set, frozenset)


class TestEmbeddingModeGuard:
    """A semantic-indexing mode must not load without something to embed with.

    `RAG_EMBEDDING_MODE=qroq` otherwise starts cleanly and fails at the first
    ingest, where the message reads like a bad document — and by then the
    deployment believes it indexes semantically.
    """

    def test_qroq_mode_without_a_model_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="QROQ_EMBEDDING_MODEL"):
            _settings(rag_embedding_mode="qroq", qroq_embedding_model="")

    def test_qroq_mode_with_a_model_loads(self) -> None:
        s = _settings(rag_embedding_mode="qroq", qroq_embedding_model="some-model")
        assert s.rag_embedding_mode == "qroq"

    def test_none_mode_needs_no_model(self) -> None:
        assert _settings().rag_embedding_mode == "none"

    def test_no_embedding_model_is_claimed_by_default(self) -> None:
        """The provider served no embedding model when probed (2026-10-06).

        A default naming one would read as configured, and would fail an ingest
        later with an error blaming the document.
        """
        assert _settings().qroq_embedding_model == ""

    def test_fast_model_default_is_not_the_retired_id(self) -> None:
        """`llama-3.3-70b-versatile` was never in the provider's model list."""
        assert _settings().qroq_fast_model != "llama-3.3-70b-versatile"


class TestRiskDefaults:
    def test_llm_concurrency_is_conservative_for_free_tier(self) -> None:
        """D-004: Qroq free tier throttles. Concurrency must stay low."""
        assert _settings().llm_max_concurrency <= 4

    def test_schema_repair_is_bounded(self) -> None:
        """D-004 forbids silent fallthrough to the execution layer."""
        assert 1 <= _settings().llm_schema_repair_attempts <= 3

    def test_dramatiq_retries_are_bounded(self) -> None:
        """Section 21 forbids unbounded retries."""
        assert 1 <= _settings().dramatiq_max_retries <= 5

    def test_no_langsmith_key_is_modelled(self) -> None:
        """D-007 prohibits third-party observability."""
        assert not any("langsmith" in f for f in Settings.model_fields)

class TestAsyncDatabaseUrl:
    """The dialect asyncpg speaks, translated from the one we document.

    Every query in this application is async. `DATABASE_URL` is validated,
    documented and stored in libpq's dialect because that is what Neon
    documents and what a `psql` session can use — and handing that string
    straight to an async engine builds a *synchronous* psycopg engine, which
    fails on the first connect. Phase 25 found it by running the documented
    value against a real server; nothing else could, because every test
    either passed an already-translated URL or replaced the session
    dependency.
    """

    def test_the_scheme_names_the_async_driver(self) -> None:
        assert (
            async_database_url("postgresql://u:p@host/db?sslmode=require")
            == "postgresql+asyncpg://u:p@host/db?ssl=require"
        )

    def test_the_short_scheme_is_rewritten_too(self) -> None:
        assert async_database_url("postgres://u:p@host/db?sslmode=require").startswith(
            "postgresql+asyncpg://"
        )

    @pytest.mark.parametrize(
        "mode", ["disable", "prefer", "require", "verify-ca", "verify-full"]
    )
    def test_every_sslmode_asyncpg_understands_is_translated(self, mode: str) -> None:
        assert f"ssl={mode}" in async_database_url(f"postgresql://u:p@h/db?sslmode={mode}")

    def test_other_query_parameters_survive(self) -> None:
        translated = async_database_url(
            "postgresql://u:p@host/db?sslmode=require&application_name=cg"
        )

        assert translated == "postgresql+asyncpg://u:p@host/db?ssl=require&application_name=cg"

    def test_sslmode_allow_is_dropped_rather_than_guessed(self) -> None:
        """asyncpg has no `allow`, and assuming TLS is not ours to assume."""

        assert async_database_url("postgresql://u:p@h/db?sslmode=allow") == (
            "postgresql+asyncpg://u:p@h/db"
        )

    def test_a_url_without_sslmode_is_left_alone(self) -> None:
        assert async_database_url("postgresql://u:p@h/db") == "postgresql+asyncpg://u:p@h/db"

    def test_settings_exposes_the_translated_url(self) -> None:
        """The property an engine is built from, not the raw setting."""

        settings = Settings(
            DATABASE_URL="postgresql://u:p@host/db?sslmode=require",
            QROQ_API_KEY="x" * 8,
            ENCRYPTION_KEY="k56RU-1GjWszzDdaaTRbNKnrgc3yq-ph8dHk_eVVLPg=",
        )

        assert settings.async_database_url == "postgresql+asyncpg://u:p@host/db?ssl=require"
        # The stored value keeps the documented spelling.
        assert settings.database_url == "postgresql://u:p@host/db?sslmode=require"
