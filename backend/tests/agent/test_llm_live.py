"""The tests that need a provider (Phase 14/15, §67).

Skipped unless `CATALOGGUARD_LIVE_LLM=1` is set, because it is an opt-in spend
rather than part of the default gate: a scheduled suite that quietly calls a
third party on every run is a cost decision nobody made.

Everything else in `tests/agent` proves the loop *around* the model — the
parsing, the repair bound, the routing, the abort, the tool contract. Only this
file can say a real endpoint answered, that the schema validated against real
output, and that a real model reached for a tool. Run it with:

    CATALOGGUARD_LIVE_LLM=1 pytest tests/agent/test_llm_live.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from app.agents.client import ChatCompletionClient, ModelTier
from app.agents.state.models import IntakeReport, ReconciliationOutput, Usage
from app.agents.tools import ToolContext, ToolRegistry
from app.agents.tools.catalog import SOURCE_AUTHORITY
from app.core.config import Settings
from app.models.tenancy import UserRole

PLACEHOLDERS = ("REPLACE_ME", "dummy", "qroq-test")


def _live_key() -> str | None:
    """The real key from the repo `.env`.

    `tests/conftest.py` installs a fake `QROQ_API_KEY` before anything imports
    the app, so the offline suite can never spend a real credential — which is
    correct, and which is exactly why this test cannot ask `get_settings()`.
    It reads the file instead, and declines when the key there is not a key.
    """
    for candidate in (Path("../.env"), Path(".env")):
        if not candidate.is_file():
            continue
        for line in candidate.read_text().splitlines():
            if line.startswith("QROQ_API_KEY="):
                value = line.partition("=")[2].strip()
                if value and not value.startswith(PLACEHOLDERS):
                    return value
    return None


LIVE_KEY = _live_key()


def _client() -> ChatCompletionClient:
    assert LIVE_KEY is not None
    return ChatCompletionClient(settings=Settings(_env_file=None, qroq_api_key=LIVE_KEY))


pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        os.getenv("CATALOGGUARD_LIVE_LLM") != "1",
        reason="opt-in live provider call; set CATALOGGUARD_LIVE_LLM=1",
    ),
    pytest.mark.skipif(LIVE_KEY is None, reason="no live QROQ_API_KEY in .env"),
]

INTAKE_USER = (
    "Workspace: ws-live\n"
    "Catalog: cat-live\n"
    "Products supplied: 2\n"
    "Conflicts supplied: 1\n"
    "Attribute keys seen: colour, size, material\n"
    "First product external id: SKU-1\n"
    "Disagreed fields: color"
)


class TestLiveProvider:
    async def test_a_real_completion_validates(self) -> None:
        """The fast tier answers, and the answer validates against our schema."""
        client = _client()
        try:
            report, usage = await client.complete(
                tier=ModelTier.FAST,
                schema=IntakeReport,
                system=(
                    "You are the catalog intake agent. Identify the source of the "
                    "payload, classify what it contains, and report problems with it."
                ),
                user=INTAKE_USER,
            )
        finally:
            # Each test runs on its own event loop; a transport that outlives its
            # loop breaks the next one, so the client is released before return.
            await client.aclose()

        assert isinstance(report, IntakeReport)
        assert isinstance(usage, Usage)
        assert usage.calls >= 1
        assert usage.total_tokens > 0, "the provider reported no usage"
        assert report.record_count >= 0

    async def test_the_reasoning_tier_answers_too(self) -> None:
        """§35 routing is only real if the reasoning model also serves."""
        client = _client()

        try:
            result, usage = await client.complete(
                tier=ModelTier.REASONING,
                schema=ReconciliationOutput,
                system=(
                    "You are the catalog reconciliation agent. Propose values for "
                    "disagreements, or leave a conflict unresolved when the evidence "
                    "does not settle it."
                ),
                user=(
                    "Disagreements to resolve:\n"
                    "- c1 | color | candidates: red | blue | evidence: two sources disagree"
                ),
            )
        finally:
            await client.aclose()

        assert isinstance(result, ReconciliationOutput)
        assert isinstance(usage, Usage)
        # Either an answer or an explicit non-answer — both are valid shapes, and
        # a model that resolves nothing must name the conflict it could not.
        proposed = {proposal.conflict_id for proposal in result.proposals}
        assert proposed <= {"c1"}
        assert proposed or "c1" in result.unresolved_conflicts



class AuthorityAnswer(BaseModel):
    """What the tool would have said, in the shape §36 asks for."""

    decision: str = Field(max_length=60)
    winner_source: str | None = Field(default=None, max_length=40)
    reason: str = Field(max_length=500)


class TestLiveToolCall:
    async def test_a_real_model_reaches_for_a_tool(self) -> None:
        """§23: a tool only exists if a model that has one actually uses it.

        The offline suite proves the tool answers correctly when asked, and that
        the loop carries a scripted call. Neither says a live model will choose
        to call — that is the whole claim a tool layer makes.
        """
        client = _client()
        registry = ToolRegistry([SOURCE_AUTHORITY])
        try:
            answer, usage = await client.complete(
                tier=ModelTier.FAST,
                schema=AuthorityAnswer,
                system=(
                    "You are the catalog reconciliation agent. You must call the "
                    "resolve_source_authority tool to answer; do not answer from "
                    "memory. Then reply with one JSON object."
                ),
                user=(
                    "Field: gtin\n"
                    "Candidates:\n"
                    "- source: marketplace, value: 1111111111111\n"
                    "- source: manufacturer, value: 2222222222222"
                ),
                tools=registry,
                tool_context=ToolContext(
                    workspace_id="ws-live", role=UserRole.DATA_STEWARD
                ),
            )
        finally:
            await client.aclose()

        assert isinstance(answer, AuthorityAnswer)
        # A tool round happened before the answer: the model asked, and only
        # then replied. One call would mean it answered without looking.
        assert usage.calls >= 2, "the model answered without calling the tool"
        assert usage.input_tokens > 0
        assert answer.decision == "resolved"
        assert answer.winner_source == "manufacturer"
