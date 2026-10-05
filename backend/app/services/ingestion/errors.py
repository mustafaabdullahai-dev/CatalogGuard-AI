"""Structured ingestion errors (§54).

§54 asks for a machine-readable code, a human message, a details bag, and a
`recoverable` flag rather than a bare string. Ingestion is where that earns its
keep: a rejected upload has to tell an operator whether to fix the file and
retry (`recoverable`) or change something structural first (not recoverable),
because the two lead to completely different next actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class IngestionErrorCode(StrEnum):
    """Rejection reasons. Stable strings, so a client can branch on them."""

    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    EMPTY_FILE = "EMPTY_FILE"
    ENCODING_UNDETECTABLE = "ENCODING_UNDETECTABLE"
    MALFORMED_FILE = "MALFORMED_FILE"
    SECURITY_REJECTED = "SECURITY_REJECTED"
    STRUCTURAL_LIMIT_EXCEEDED = "STRUCTURAL_LIMIT_EXCEEDED"
    SCHEMA_UNDETECTABLE = "SCHEMA_UNDETECTABLE"
    STORAGE_FAILED = "STORAGE_FAILED"


@dataclass
class IngestionError(Exception):
    """A rejected or failed ingestion step, in §54's shape.

    Deliberately NOT a bare `ValueError`. The caller has to reconstruct a
    response from `error_code`, `message`, `details`, and `recoverable`, so the
    four travel together as one object instead of being reconstructed by string
    matching at each call site.
    """

    code: IngestionErrorCode
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    recoverable: bool = True

    def __str__(self) -> str:  # pragma: no cover - trivial formatting
        return f"{self.code.value}: {self.message}"

    def as_dict(self) -> dict[str, Any]:
        """The §54 error envelope, ready to embed in a response body."""
        return {
            "error_code": self.code.value,
            "message": self.message,
            "details": self.details,
            "recoverable": self.recoverable,
        }
