"""Telemetry: a port, a null adapter, and a self-hosted Langfuse adapter.

D-010 decided where traces go — a self-hosted Langfuse inside our own perimeter,
never a third-party SaaS — and what must never go with them: secrets, and the
masked keys the logging layer already treats as commercial data.

What Phase 23 adds is the missing middle: a seam that the rest of the
application can emit into without knowing which destination is configured. The
seam is the same shape Phase 10's mail port and Phase 19's connector registry
used, and for the same reason — a port with a null adapter is honest while the
deployment it will point at does not exist yet.

    app emits TelemetryEvent -> TraceSink (protocol)
                                  ├── NullTraceSink     (default; counts, discards)
                                  └── LangfuseTraceSink (self-hosted; env-gated)

Three rules hold for every event, and the tests assert them on the payload rather
than on the adapter:

1. **No content.** Events carry counts, durations, statuses, model ids and
   retrieval *signals* — never a prompt, a query, a product title or a document
   chunk. A trace that reproduces catalog text would defeat D-010's masking rule
   one layer up.
2. **No secrets.** Nothing here reads a key, a token or a password, and the
   Langfuse adapter's credentials come from settings at emit time and never
   appear in an event.
3. **Never raises.** A telemetry destination that is down must not fail the
   operation that was being observed — the same rule the mail outbox and the
   tool-call recorder follow.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.masking import mask_value

logger = get_logger(__name__)

EventKind = Literal[
    "agent_run",
    "tool_call",
    "llm_call",
    "retrieval",
    "execution",
]


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    """One observation. Counts and timings, never content.

    `attributes` is deliberately flat and small: an unbounded dict here is how a
    catalog field ends up inside a third party's database.
    """

    kind: EventKind
    name: str
    at: datetime
    duration_ms: int | None = None
    status: str = "ok"
    workspace_id: str | None = None
    request_id: str | None = None
    run_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "at": self.at.isoformat(),
            "duration_ms": self.duration_ms,
            "status": self.status,
            "workspace_id": self.workspace_id,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "attributes": mask_value(dict(self.attributes), _EXTRA_MASK_KEYS),
        }


# Anything a future caller might attach that is catalog content rather than a
# count. The logging layer masks its own configured keys; this adds the shape
# names a trace is most likely to reach for.
_EXTRA_MASK_KEYS = frozenset(
    {
        "arguments",
        "args",
        "body",
        "chunk",
        "content",
        "description",
        "document_text",
        "input",
        "note",
        "notes",
        "output",
        "prompt",
        "query",
        "reason",
        "result_summary",
        "sku",
        "text",
        "title",
        "value",
    }
)


@runtime_checkable
class TraceSink(Protocol):
    """Where observations go. Implementations must never raise."""

    def emit(self, event: TelemetryEvent) -> None: ...

    def flush(self) -> None: ...


class NullTraceSink:
    """The default: keep a bounded sample for tests, send nothing.

    A count is kept rather than nothing at all, because "no events were emitted"
    and "events were emitted and dropped" must be distinguishable when someone
    asks why a trace is missing.
    """

    def __init__(self, keep_last: int = 50) -> None:
        self.emitted = 0
        self.recent: list[TelemetryEvent] = []
        self._keep_last = keep_last

    def emit(self, event: TelemetryEvent) -> None:
        self.emitted += 1
        self.recent.append(event)
        if len(self.recent) > self._keep_last:
            del self.recent[0]

    def flush(self) -> None:
        return None


class LangfuseTraceSink:
    """Post observations to a self-hosted Langfuse (D-010's only destination).

    Unverified in this repository: no Langfuse instance has ever been reachable
    (D-006, and the deployment target does not exist until Phase 28). What is
    verified is the payload's shape, the credential handling, and the rule that a
    failure here is logged and swallowed — an observability backend must not be
    able to fail a product change.
    """

    def __init__(
        self,
        *,
        host: str,
        public_key: str,
        secret_key: str,
        timeout_seconds: float = 2.0,
        transport: Callable[[str, bytes, dict[str, str], float], None] | None = None,
    ) -> None:
        if not (host and public_key and secret_key):
            raise ValueError("a Langfuse sink needs host, public_key and secret_key")
        self.host = host.rstrip("/")
        # Basic auth with the two keys, the way Langfuse's ingestion API takes
        # them. Held in the instance and never in an event or a log line.
        self._auth = f"Basic {_basic(public_key, secret_key)}"
        self._timeout = timeout_seconds
        self._transport = transport or _post_json

    def emit(self, event: TelemetryEvent) -> None:
        payload = json.dumps(_to_langfuse_event(event)).encode()
        try:
            self._transport(
                f"{self.host}/api/public/ingestion",
                payload,
                {"Authorization": self._auth},
                self._timeout,
            )
        except Exception as exc:  # noqa: BLE001 - telemetry must never break a request
            logger.warning(
                "telemetry_emit_failed",
                sink="langfuse",
                kind=event.kind,
                error_type=type(exc).__name__,
            )

    def flush(self) -> None:
        return None


def _basic(public_key: str, secret_key: str) -> str:
    import base64

    return base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()


def _post_json(url: str, body: bytes, headers: dict[str, str], timeout: float) -> None:
    # Plaintext is refused before anything is built: a trace leaving over http to
    # a remote host would put commercial data on the wire, which is the one
    # thing D-010 rules out. Loopback is allowed because that is how a
    # self-hosted Langfuse is reached before TLS terminates in front of it.
    if not url.startswith("https://") and not _is_loopback(url):
        raise ValueError("refusing to send telemetry over plaintext to a remote host")
    request = urllib.request.Request(  # noqa: S310 - scheme checked above
        url, data=body, headers={"content-type": "application/json", **headers}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        response.read()


def _is_loopback(url: str) -> bool:
    return url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))


def _to_langfuse_event(event: TelemetryEvent) -> dict[str, Any]:
    """Map our event onto Langfuse's ingestion shape.

    `input` and `output` are counts by construction, never text: this adapter is
    where a future contributor would be tempted to attach a prompt, so the field
    names are deliberately the only place content could enter.
    """
    payload = event.as_dict()
    return {
        "id": f"{event.kind}-{event.name}-{event.at.timestamp()}",
        "timestamp": event.at.isoformat(),
        "type": "span-create",
        "body": {
            "name": f"{event.kind}.{event.name}",
            "metadata": {
                "status": event.status,
                "workspace_id": event.workspace_id,
                "request_id": event.request_id,
                "agent_run_id": event.run_id,
                **payload["attributes"],
            },
            "input": {"kind": event.kind, "name": event.name},
            "output": {"status": event.status, "duration_ms": event.duration_ms},
            # Case-insensitive on purpose: execution statuses are upper case
            # (`FAILED`, `ROLLED_BACK`) while tool outcomes are lower, and a
            # failed job traced at DEFAULT level is an incident nobody sees.
            "level": (
                "ERROR"
                if event.status.strip().lower() in ("failed", "error", "refused")
                else "DEFAULT"
            ),
            "startTime": event.at.isoformat(),
            "endTime": event.at.isoformat(),
        },
    }


# ---------------------------------------------------------------------------
# configuration


_sink: TraceSink | None = None
_sink_lock = threading.Lock()


def trace_sink(settings: Settings | None = None) -> TraceSink:
    """The configured sink, built once.

    Configuration decides which implementation this is, so no call site ever
    branches on "is observability enabled" — the same reasoning behind the mail
    and connector ports.
    """
    global _sink
    with _sink_lock:
        if _sink is not None:
            return _sink
        config = settings or get_settings()
        _sink = _build(config)
        return _sink


def set_trace_sink(sink: TraceSink | None) -> None:
    """Install a sink explicitly. Test hook and the DI seam for Phase 28."""
    global _sink
    with _sink_lock:
        _sink = sink


def reset_trace_sink() -> None:
    set_trace_sink(None)


def _build(settings: Settings) -> TraceSink:
    if not settings.langfuse_enabled:
        return NullTraceSink()
    configured = (
        settings.langfuse_host,
        settings.langfuse_public_key,
        settings.langfuse_secret_key,
    )
    if not all(configured):
        logger.warning(
            "langfuse_enabled_without_credentials",
            detail="traces are dropped; set host and both keys or disable it",
        )
        return NullTraceSink()
    try:
        return LangfuseTraceSink(
            host=settings.langfuse_host,
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            timeout_seconds=settings.telemetry_timeout_seconds,
        )
    except ValueError as exc:
        logger.warning("langfuse_sink_unusable", error_type=type(exc).__name__)
        return NullTraceSink()


def emit(
    kind: EventKind,
    name: str,
    *,
    duration_ms: int | None = None,
    status: str = "ok",
    workspace_id: str | None = None,
    request_id: str | None = None,
    run_id: str | None = None,
    attributes: dict[str, Any] | None = None,
    at: datetime | None = None,
    sink: TraceSink | None = None,
) -> None:
    """Emit one observation. Never raises, whatever the sink does."""
    target = sink if sink is not None else trace_sink()
    try:
        target.emit(
            TelemetryEvent(
                kind=kind,
                name=name,
                at=at or datetime.now(UTC),
                duration_ms=duration_ms,
                status=status,
                workspace_id=workspace_id,
                request_id=request_id,
                run_id=run_id,
                attributes=dict(attributes or {}),
            )
        )
    except Exception as exc:  # noqa: BLE001 - the observation is optional
        logger.warning("telemetry_emit_failed", kind=kind, error_type=type(exc).__name__)


def current_request_id() -> str | None:
    """The bound request id, so an emit can be stitched to a log line."""
    from structlog.contextvars import get_contextvars

    value = get_contextvars().get("request_id")
    return None if value is None else str(value)


__all__ = [
    "LangfuseTraceSink",
    "NullTraceSink",
    "TelemetryEvent",
    "TraceSink",
    "current_request_id",
    "emit",
    "reset_trace_sink",
    "set_trace_sink",
    "trace_sink",
]