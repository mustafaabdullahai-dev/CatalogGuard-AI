"""Execution of approved changes (Phase 20, §21, §51).

One entry point. `execute_proposal` is the only function in the codebase that
calls a marketplace to change something, and `ExecutionRefusedError` is raised
before a job exists whenever nothing was attempted.
"""

from __future__ import annotations

from app.services.execution.engine import (
    DEFAULT_MAX_ATTEMPTS,
    OPERATION,
    ExecutionRefusedError,
    VerificationError,
    execute_proposal,
)

__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "OPERATION",
    "ExecutionRefusedError",
    "VerificationError",
    "execute_proposal",
]
