"""Edge masking for sensitive catalog data (D-010).

Mandatory safeguard. Applied at the serialization boundary so it covers
structured logs, Langfuse traces, error payloads, and exception context
uniformly. Masking at the log-handler layer alone would leak through
telemetry that bypasses logging.
"""

from __future__ import annotations

import re
from typing import Any

MASK = "***REDACTED***"

# Values that look like secrets regardless of key name.
# Underscore is included in the token class on purpose: `\b` does not fire
# between "_" and an alphanumeric, so a prefixed key such as
# "gsk_" + 48 chars would otherwise evade a [A-Za-z0-9] class entirely.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9])"),  # tokens / API keys
    re.compile(r"(?<!\d)\d{12,19}(?!\d)"),  # card numbers, long numeric ids
)

_SENSITIVE_KEY_RE = re.compile(
    r"(pass(word)?|secret|token|api[_-]?key|authorization|cookie|"
    r"supplier_cost|unreleased)",
    re.IGNORECASE,
)


def mask_text(value: str, extra_keys: frozenset[str] | None = None) -> str:
    """Mask secret-looking substrings in free text."""
    keys = extra_keys or frozenset()
    for key in keys:
        value = re.sub(
            rf"(\b{re.escape(key)}\b\s*[:=]\s*)\S+",
            rf"\1{MASK}",
            value,
            flags=re.IGNORECASE,
        )
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(MASK, value)
    return value


def mask_value(value: Any, sensitive_keys: frozenset[str] | None = None) -> Any:
    """Recursively mask sensitive values in arbitrary structures.

    Matching is case-insensitive and substring-based, so `sku_id`,
    `supplierCost`, and `unit_price` variants are all caught.

    Sequences are traversed positionally, so values inside a list or tuple
    are only masked on value shape, never by key. Pass keyed structures
    (dicts) when masking catalog fields.
    """
    keys = sensitive_keys or frozenset()

    if isinstance(value, dict):
        masked: dict[Any, Any] = {}
        for k, v in value.items():
            key_str = str(k)
            is_secret_key = _SENSITIVE_KEY_RE.search(key_str) is not None
            is_configured_key = bool(keys) and any(s in key_str.lower() for s in keys)
            if is_secret_key or is_configured_key:
                masked[k] = MASK
            else:
                masked[k] = mask_value(v, keys)
        return masked

    if isinstance(value, list | tuple | set):
        return [mask_value(item, keys) for item in value]

    if isinstance(value, str):
        return mask_text(value, keys)

    return value