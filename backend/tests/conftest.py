"""Test-time configuration.

The application refuses to start with placeholder secrets, which is correct in
production and inconvenient in tests: importing `app.main` would need real
credentials to prove that a logo route returns a PNG.

These values are obviously fake and exist only to satisfy validation. They are
set before any test module imports the app, so the whole suite runs offline with
no `.env` and no risk of a real credential reaching a test assertion.

They pass because the environment defaults to "development", which skips the
production-only checks in `Settings`. The same values would be rejected in
production, which is the point of those checks.
"""

from __future__ import annotations

import os

# Non-placeholder so `_reject_placeholder_secret` does not fire, and
# non-secret so no fixture assertion can accidentally depend on a real value.
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-not-used-anywhere-0123456789")

# Must be a valid Fernet key: config decodes it at validation time.
os.environ.setdefault("ENCRYPTION_KEY", "k56RU-1GjWszzDdaaTRbNKnrgc3yq-ph8dHk_eVVLPg=")

os.environ.setdefault("QROQ_API_KEY", "qroq-test-key-not-a-real-credential")

os.environ.setdefault("REDIS_URL", "rediss://default:test@redis.invalid:6379/0")

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://test:test@postgres.invalid/neondb?sslmode=require",
)


# ENVIRONMENT is deliberately not set. `Settings` defaults it to
# "development", which already skips the production-only assertions, and
# forcing "test" here would leak into tests that assert on the default.
