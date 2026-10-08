"""Copilot request and response models (Phase 22, §31).

The response repeats the standing disclaimers on every call rather than leaving
them to the UI: `note` says what the copilot cannot do, and `insufficient_data`
is a first-class field so a caller can render "the tools could not answer this"
instead of an empty-looking paragraph.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MAX_HISTORY = 20
MAX_QUESTION = 4000


class ConversationTurn(BaseModel):
    """One prior question and its answer, kept for context only."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    answer: str = Field(default="", max_length=MAX_QUESTION)


class CopilotQuery(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    history: list[ConversationTurn] = Field(default_factory=list, max_length=MAX_HISTORY)


class CopilotCitationOut(BaseModel):
    tool: str
    reference: str
    detail: str = ""


class CopilotResponse(BaseModel):
    answer: str
    citations: list[CopilotCitationOut] = Field(default_factory=list)
    tools_used: list[str] = Field(default_factory=list)
    insufficient_data: bool
    follow_ups: list[str] = Field(default_factory=list)
    tool_calls: int = 0
    run_id: UUID | None = None
    model: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    note: str


class CopilotError(BaseModel):
    """A refusal the caller can render as written."""

    detail: str
    code: str