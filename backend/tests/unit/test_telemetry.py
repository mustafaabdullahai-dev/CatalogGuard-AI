"""Telemetry: the port, the two adapters, and what an event may contain.

(Phase 23, §32, D-010.)

The rules that matter are negative ones — no content, no secrets, never raises —
and a test that only checked "an event was emitted" would pass while a prompt
leaked into a third party's database. So these tests assert the payload's shape
and its masking, the credential handling, and the failure behaviour.

What is *not* verified here, and is stated on the phase page: no Langfuse
instance has ever been reachable, so the adapter's wire call is exercised through
an injected transport rather than against a server.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.telemetry import (
    LangfuseTraceSink,
    NullTraceSink,
    TelemetryEvent,
    current_request_id,
    emit,
    reset_trace_sink,
    set_trace_sink,
    trace_sink,
)


def _event(**overrides: Any) -> TelemetryEvent:
    values: dict[str, Any] = {
        "kind": "tool_call",
        "name": "conflict_search",
        "at": datetime(2026, 10, 8, 9, 0, tzinfo=UTC),
        "duration_ms": 42,
        "status": "ok",
        "workspace_id": str(uuid4()),
        "run_id": str(uuid4()),
        "attributes": {"tool": "conflict_search", "ok": True, "duration_ms": 42},
    }
    values.update(overrides)
    return TelemetryEvent(**values)


class TestEventContract:
    def test_an_event_carries_counts_and_ids_not_content(self) -> None:
        payload = _event(
            attributes={
                "returned": 5,
                "candidates": 214,
                "signals": ["keyword", "metadata"],
                "top_k": 8,
            }
        ).as_dict()

        assert payload["attributes"]["returned"] == 5
        assert payload["kind"] == "tool_call"
        assert payload["duration_ms"] == 42

    @pytest.mark.parametrize(
        "key",
        ["query", "prompt", "content", "sku", "title", "reason", "arguments", "text"],
    )
    def test_content_shaped_keys_are_masked(self, key: str) -> None:
        payload = _event(attributes={key: "Navy Ceramic Mug 350ml, GTIN 5012345678900"}).as_dict()

        assert "5012345678900" not in json.dumps(payload)
        assert "Navy Ceramic Mug" not in json.dumps(payload)

    def test_a_masked_attribute_key_keeps_its_shape(self) -> None:
        payload = _event(attributes={"query": "material claims", "returned": 3}).as_dict()

        assert payload["attributes"]["returned"] == 3
        assert payload["attributes"]["query"] != "material claims"


class TestSinks:
    def test_the_null_sink_counts_and_discards(self) -> None:
        sink = NullTraceSink()

        sink.emit(_event())
        sink.emit(_event())

        assert sink.emitted == 2
        assert len(sink.recent) == 2

    def test_the_null_sink_keeps_only_a_bounded_sample(self) -> None:
        sink = NullTraceSink(keep_last=2)

        for index in range(5):
            sink.emit(_event(name=f"call-{index}"))

        assert [event.name for event in sink.recent] == ["call-3", "call-4"]

    def test_emit_never_raises_when_the_sink_does(self) -> None:
        class Exploding:
            def emit(self, event: TelemetryEvent) -> None:
                raise RuntimeError("sink down")

            def flush(self) -> None:
                raise RuntimeError("sink down")

        emit("agent_run", "finished", sink=Exploding())  # no exception escapes

    def test_emit_defaults_to_the_configured_sink(self) -> None:
        recorded: list[TelemetryEvent] = []

        class Recorder:
            def emit(self, event: TelemetryEvent) -> None:
                recorded.append(event)

            def flush(self) -> None:
                return None

        set_trace_sink(Recorder())
        try:
            emit("execution", "update_product.succeeded", status="SUCCEEDED")
        finally:
            reset_trace_sink()

        assert [event.kind for event in recorded] == ["execution"]


class TestConfiguration:
    def test_tracing_is_off_by_default(self) -> None:
        reset_trace_sink()
        try:
            assert isinstance(trace_sink(Settings(langfuse_enabled=False)), NullTraceSink)
        finally:
            reset_trace_sink()

    def test_enabled_without_credentials_falls_back_and_says_so(self) -> None:
        reset_trace_sink()
        try:
            sink = trace_sink(Settings(langfuse_enabled=True, langfuse_host=""))
            assert isinstance(sink, NullTraceSink)
        finally:
            reset_trace_sink()

    def test_configured_credentials_build_the_langfuse_sink(self) -> None:
        reset_trace_sink()
        settings = Settings(
            langfuse_enabled=True,
            langfuse_host="https://langfuse.internal",
            langfuse_public_key="pk-test",
            langfuse_secret_key="sk-test",
        )
        try:
            sink = trace_sink(settings)
            assert isinstance(sink, LangfuseTraceSink)
            assert sink.host == "https://langfuse.internal"
        finally:
            reset_trace_sink()

    def test_an_incomplete_configuration_never_builds_a_sink(self) -> None:
        with pytest.raises(ValueError):
            LangfuseTraceSink(host="https://langfuse.internal", public_key="", secret_key="")


class TestLangfuseAdapter:
    def test_it_posts_to_the_ingestion_endpoint_with_basic_auth(self) -> None:
        sent: list[tuple[str, bytes, dict[str, str], float]] = []
        sink = LangfuseTraceSink(
            host="https://langfuse.internal/",
            public_key="pk-test",
            secret_key="sk-test",
            transport=lambda url, body, headers, timeout: sent.append(
                (url, body, headers, timeout)
            ),
        )

        sink.emit(_event(name="compliance_search", kind="retrieval", status="empty"))

        url, body, headers, _timeout = sent[0]
        assert url == "https://langfuse.internal/api/public/ingestion"
        assert headers["Authorization"].startswith("Basic ")
        payload = json.loads(body)
        assert payload["type"] == "span-create"
        assert payload["body"]["name"] == "retrieval.compliance_search"

    def test_no_credential_appears_in_the_payload(self) -> None:
        sent: list[bytes] = []
        sink = LangfuseTraceSink(
            host="https://langfuse.internal",
            public_key="pk-secret-value",
            secret_key="sk-secret-value",
            transport=lambda _url, body, _headers, _timeout: sent.append(body),
        )

        sink.emit(_event())

        assert b"sk-secret-value" not in sent[0]
        assert b"pk-secret-value" not in sent[0]

    def test_a_failing_transport_is_logged_not_raised(self) -> None:
        def _boom(_url: str, _body: bytes, _headers: dict[str, str], _timeout: float) -> None:
            raise TimeoutError("langfuse unreachable")

        sink = LangfuseTraceSink(
            host="https://langfuse.internal",
            public_key="pk",
            secret_key="sk",
            transport=_boom,
        )

        sink.emit(_event())  # no exception

    def test_a_failed_event_is_levelled_as_an_error(self) -> None:
        sent: list[bytes] = []
        sink = LangfuseTraceSink(
            host="https://langfuse.internal",
            public_key="pk",
            secret_key="sk",
            transport=lambda _url, body, _headers, _timeout: sent.append(body),
        )

        sink.emit(_event(kind="execution", status="FAILED"))

        assert json.loads(sent[0])["body"]["level"] == "ERROR"

    def test_plaintext_to_a_remote_host_is_refused(self) -> None:
        from app.core.telemetry import _post_json

        with pytest.raises(ValueError, match="plaintext"):
            _post_json("http://langfuse.example.com/api", b"{}", {}, 0.1)


class TestRequestBinding:
    def test_the_request_id_is_read_from_the_log_context(self) -> None:
        from structlog.contextvars import bind_contextvars, clear_contextvars

        clear_contextvars()
        bind_contextvars(request_id="abc123")
        try:
            assert current_request_id() == "abc123"
        finally:
            clear_contextvars()

    def test_without_a_request_there_is_no_id(self) -> None:
        from structlog.contextvars import clear_contextvars

        clear_contextvars()

        assert current_request_id() is None