"""Shared response models.

`ErrorResponse` exists because Phase 24's §42 contract test found routes that
documented only success: FastAPI adds a 422 automatically for routes with a
body, so every other route looked documented while a client had no declared way
to learn it was unauthenticated. Documenting a failure means naming the model
that describes it, and this is that model.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    """The one shape every refusal takes.

    FastAPI's `HTTPException` detail is a string, and Phase 20's execution
    refusals and Phase 22's copilot refusals both answered with strings too, so
    this is the wire shape a client parses for every 4xx and 5xx on this API.
    """

    model_config = ConfigDict(extra="allow")

    detail: str = Field(description="What went wrong, in the caller's terms")