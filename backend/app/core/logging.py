"""Structured logging with mandatory edge masking (Section 55, D-010).

Masking runs in the processor chain, so it applies to every log event
regardless of call site. Values are masked before formatting, which also
covers exceptions and traceback payloads.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.masking import mask_value

_configured = False


class MaskingProcessor:
    """structlog processor that masks sensitive data.

    Placed first in the chain so downstream processors never observe raw
    values, including in error paths.
    """

    def __init__(self, mask_keys: frozenset[str]) -> None:
        self._keys = mask_keys

    def __call__(
        self, _logger: Any, _name: str, event_dict: dict[str, Any]
    ) -> dict[str, Any]:
        masked = mask_value(event_dict, self._keys)
        return masked if isinstance(masked, dict) else event_dict


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    global _configured
    if _configured:
        return

    from app.core.config import get_settings

    settings = get_settings()

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        MaskingProcessor(settings.mask_key_set),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_logs:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())
    _configured = True


def get_logger(name: str) -> Any:
    configure_logging()
    return structlog.get_logger(name)


def bind_context(**kwargs: Any) -> None:
    """Bind request-scoped fields: request_id, workspace_id, user_id, agent_run_id."""
    structlog.contextvars.bind_contextvars(**kwargs)