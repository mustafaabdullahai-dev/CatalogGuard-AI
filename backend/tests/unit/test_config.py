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

from app.core.config import Settings

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