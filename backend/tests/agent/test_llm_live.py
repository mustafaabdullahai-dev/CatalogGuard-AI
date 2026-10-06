"""The one test that needs a provider (Phase 14, §67).

Skipped unless `CATALOGGUARD_LIVE_LLM=1` is set, because it is an opt-in spend
rather than part of the default gate: a scheduled suite that quietly calls a
third party on every run is a cost decision nobody made.

It exists because everything else in `tests/agent` proves the loop *around* the
model — the parsing, the repair bound, the routing, the abort. Only this file
can say a real endpoint answered and the schema actually validated against real
output. Run it with:

    CATALOGGUARD_LIVE_LLM=1 pytest tests/agent/test_llm_live.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.agents.client import ChatCompletionClient, ModelTier
from app.agents.state.models import IntakeReport, ReconciliationOutput, Usage
from app.core.config import Settings

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
