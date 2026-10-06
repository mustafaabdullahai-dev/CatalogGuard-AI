"""Graph and validation-layer tests (Phase 14, §6, §7, §24, §36, D-007).

Everything here runs with scripted replies and no network, which is the point:
the part of Phase 14 this codebase owns is the validation loop, the routing and
the failure behaviour, and those must be provable without a provider. The one
test that does need a provider is marked `live_llm` and skipped unless it is
explicitly asked for — see `test_llm_live.py`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

import pytest
from cryptography.fernet import Fernet
from langchain_core.messages import BaseMessage

from app.agents.client import (
    ChatCompletionClient,
    Completion,
    ModelTier,
    StructuredOutputError,
)
from app.agents.graph import run_graph
from app.agents.state.models import (
    CatalogState,
    ConflictObservation,
    FieldMapping,
    IntakeReport,
    NormalizationPlan,
    ProductObservation,
    ProposedChange,
    ReconciliationOutput,
    Usage,
)
from app.core.config import Settings
from app.models.tenancy import UserRole

FAST_MODEL = "openai/gpt-oss-20b"
REASONING_MODEL = "openai/gpt-oss-120b"

# Test-only secrets, generated rather than committed.
_FERNET = Fernet.generate_key().decode()


def _settings(**overrides: Any) -> Any:
    base = {
        "database_url": "postgresql://u:p@host.neon.tech:5432/db?sslmode=require",
        "redis_url": "rediss://default:pw@host.upstash.io:6379/0",
        "qroq_api_key": "test-key",
        "jwt_secret": "x" * 48,
        "encryption_key": _FERNET,
        "qroq_fast_model": FAST_MODEL,
        "qroq_reasoning_model": REASONING_MODEL,
    }
    return Settings(_env_file=None, **{**base, **overrides})

INTAKE_OK = IntakeReport(
    source_type="manual_entry",
    record_count=2,
    classification="apparel catalog",
).model_dump_json()

NORMALIZE_OK = NormalizationPlan(
    mappings=[
        FieldMapping(
            source_field="colour",
            canonical_field="color",
            confidence=0.9,
            evidence="documented synonym",
        )
    ],
    unmapped_fields=["legacy_code"],
).model_dump_json()

ANALYZE_OK = ReconciliationOutput(
    proposals=[
        ProposedChange(
            conflict_id="c1",
            field="color",
            current_value="red",
            proposed_value="blue",
            confidence=0.8,
            evidence="both sources list the SKU as blue",
            reason="the newer source wins under source authority",
            source=REASONING_MODEL,
        )
    ],
    unresolved_conflicts=["c2"],
).model_dump_json()


class ScriptedCompleter:
    """Replays prepared replies in order and records what it was asked.

    A script item is either text (the common case) or a whole `Completion`, so a
    test can make the model ask for a tool before it answers.
    """

    def __init__(
        self,
        replies: Sequence[str | Completion],
        *,
        input_tokens: int = 100,
        output_tokens: int = 30,
    ) -> None:
        self._replies = list(replies)
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self.calls: list[dict[str, Any]] = []
        self._in_flight = 0
        self.peak_concurrency = 0

    async def __call__(
        self,
        *,
        model: str,
        messages: Sequence[BaseMessage],
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> Completion:
        self._in_flight += 1
        self.peak_concurrency = max(self.peak_concurrency, self._in_flight)
        try:
            await asyncio.sleep(0)
            self.calls.append(
                {
                    "model": model,
                    "prompt": "\n".join(str(m.content) for m in messages),
                    "tools": None if tools is None else [t["function"]["name"] for t in tools],
                }
            )
            if not self._replies:
                raise AssertionError(f"no scripted reply left for call {len(self.calls)}")
            reply = self._replies.pop(0)
            if isinstance(reply, Completion):
                return reply.model_copy(
                    update={
                        "input_tokens": self._input_tokens,
                        "output_tokens": self._output_tokens,
                    }
                )
            return Completion(
                text=reply,
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
            )
        finally:
            self._in_flight -= 1


def state(**overrides: Any) -> CatalogState:
    base: CatalogState = {
        "workspace_id": "ws-1",
        "catalog_id": "cat-1",
        "initiator_role": UserRole.DATA_STEWARD,
        "products": [
            ProductObservation(
                external_id="SKU-1",
                title="Blue shirt",
                attributes={"colour": "blue"},
            )
        ],
        "conflicts": [
            ConflictObservation(
                conflict_id="c1",
                field="color",
                candidates=["red", "blue"],
                evidence="two sources disagree",
            )
        ],
    }
    base.update(overrides)
    return base


def client(completer: ScriptedCompleter, **overrides: Any) -> ChatCompletionClient:
    return ChatCompletionClient(
        settings=_settings(**overrides),
        completer=completer,
    )


class TestGraphRun:
    async def test_happy_path_completes_with_validated_proposals(self) -> None:
        """Three nodes, three validated objects, one completed run."""
        completer = ScriptedCompleter([INTAKE_OK, NORMALIZE_OK, ANALYZE_OK])

        result = await run_graph(state(), llm=client(completer))

        assert result["status"] == "completed"
        assert result["intake"].source_type == "manual_entry"
        assert result["normalization"].mappings[0].canonical_field == "color"
        assert [p.conflict_id for p in result["proposed_changes"]] == ["c1"]
        assert result["unresolved_conflicts"] == ["c2"]
        assert result.get("errors") in (None, [])

    async def test_each_tier_routes_to_its_own_model(self) -> None:
        """§35: classification and normalization are not worth the reasoning model."""
        completer = ScriptedCompleter([INTAKE_OK, NORMALIZE_OK, ANALYZE_OK])

        await run_graph(state(), llm=client(completer))

        assert [call["model"] for call in completer.calls] == [
            FAST_MODEL,
            FAST_MODEL,
            REASONING_MODEL,
        ]

    async def test_usage_totals_every_call(self) -> None:
        """§56 / D-007 #5: the graph reports what its run consumed."""
        completer = ScriptedCompleter([INTAKE_OK, NORMALIZE_OK, ANALYZE_OK])

        result = await run_graph(state(), llm=client(completer))

        usage = result["llm_usage"]
        assert usage.calls == 3
        assert usage.repairs == 0
        assert usage.input_tokens == 300
        assert usage.output_tokens == 90

    async def test_repair_round_is_counted_in_usage(self) -> None:
        """A repair re-spends the input; cost control must see it (D-007 #5)."""
        completer = ScriptedCompleter(["not json at all", INTAKE_OK, NORMALIZE_OK, ANALYZE_OK])

        result = await run_graph(state(), llm=client(completer))

        assert result["status"] == "completed"
        assert result["llm_usage"].calls == 4
        assert result["llm_usage"].repairs == 1

    async def test_repair_receives_the_validation_error(self) -> None:
        """D-007 #2: re-ask *with* the error, not with the same prompt again."""
        bad_first = '{"source_type": "nope"}'
        completer = ScriptedCompleter([bad_first, INTAKE_OK, NORMALIZE_OK, ANALYZE_OK])

        await run_graph(state(), llm=client(completer))

        assert "did not validate" in completer.calls[1]["prompt"]
        assert "source_type" in completer.calls[1]["prompt"]

    async def test_exhausted_repairs_abort_the_run_without_falling_through(self) -> None:
        """D-007 #3: after bounded repairs the run fails loudly and stops."""
        completer = ScriptedCompleter(["{not json", "{still not json", "{nope"])

        result = await run_graph(
            state(),
            llm=client(completer, llm_schema_repair_attempts=2),
        )

        assert result["status"] == "aborted"
        assert "proposed_changes" not in result, "an unvalidated value reached a later node"
        error = result["errors"][0]
        assert error.node == "intake"
        assert error.code == "structured_output_failed"
        assert error.attempts == 3
        # The failed call still cost tokens, and §56 counts them.
        assert error.tokens == 3 * (100 + 30)
        # Intake failed, so normalize never ran: one node, three attempts.
        assert len(completer.calls) == 3

    async def test_invalid_input_stops_before_normalization(self) -> None:
        """§7 intake validates input; a rejected payload is not normalized."""
        bad = IntakeReport(
            source_type="unknown",
            record_count=0,
            issues=[{"code": "empty_payload", "detail": "no rows", "severity": "error"}],
        ).model_dump_json()
        completer = ScriptedCompleter([bad])

        result = await run_graph(state(), llm=client(completer))

        assert result["status"] == "aborted"
        assert result["errors"][0].code == "invalid_input"
        assert "normalization" not in result
        assert len(completer.calls) == 1

    async def test_concurrency_is_bounded(self) -> None:
        """D-004: the free tier throttles, so in-flight calls are capped."""
        completer = ScriptedCompleter([INTAKE_OK, NORMALIZE_OK, ANALYZE_OK])

        await run_graph(state(), llm=client(completer, llm_max_concurrency=1))

        assert completer.peak_concurrency == 1


class TestSchemaFirstPrompt:
    async def test_the_prompt_carries_the_schema(self) -> None:
        """D-007 #4: state the schema ourselves rather than trusting the provider."""
        completer = ScriptedCompleter([INTAKE_OK, NORMALIZE_OK, ANALYZE_OK])

        await run_graph(state(), llm=client(completer))

        intake_prompt = completer.calls[0]["prompt"]
        assert "JSON Schema" in intake_prompt
        assert "source_type" in intake_prompt
        assert "JSON object and nothing else" in intake_prompt

    async def test_a_fenced_reply_is_accepted(self) -> None:
        """A fence the model added is wrapping, not a schema violation."""
        fenced = f"```json\n{INTAKE_OK}\n```"
        completer = ScriptedCompleter([fenced, NORMALIZE_OK, ANALYZE_OK])

        result = await run_graph(state(), llm=client(completer))

        assert result["status"] == "completed"
        assert result["llm_usage"].repairs == 0


class TestValidationLayer:
    async def test_error_names_the_schema_and_burn(self) -> None:
        """The exception a caller records must say what failed and what it cost."""
        completer = ScriptedCompleter(["{}", "{}"])
        llm = client(completer, llm_schema_repair_attempts=1)

        with pytest.raises(StructuredOutputError) as caught:
            await llm.complete(tier=ModelTier.FAST, schema=IntakeReport, system="s", user="u")

        assert caught.value.schema_name == "IntakeReport"
        assert caught.value.attempts == 2
        assert caught.value.input_tokens == 200
        assert "IntakeReport" in str(caught.value)

    async def test_output_is_a_model_not_a_dict(self) -> None:
        """§36: every important AI decision produces machine-readable output."""
        completer = ScriptedCompleter([INTAKE_OK])
        llm = client(completer)

        parsed, usage = await llm.complete(
            tier=ModelTier.FAST, schema=IntakeReport, system="s", user="u"
        )

        assert isinstance(parsed, IntakeReport)
        assert isinstance(usage, Usage)
        assert json.loads(INTAKE_OK)["record_count"] == parsed.record_count
