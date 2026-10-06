"""Reading a model's reply (Phase 14/15).

Shared by the validation layer and the tool layer so both agree on what "the
JSON in this reply" means and on how a `ValidationError` is described. Two
modules needing the same rule and each writing its own is how they drift.
"""

from __future__ import annotations

import re

from pydantic import ValidationError

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def json_only(text: str) -> str:
    """The JSON object in a reply, ignoring any wrapping the model added.

    The prompt asks for JSON alone; a model that fences it anyway is normal, and
    rejecting the whole reply over a fence would spend a repair round teaching it
    something the parser already knows.
    """
    fenced = _FENCE_RE.search(text)
    if fenced:
        return fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


def validation_summary(exc: ValidationError, *, limit: int = 3) -> str:
    """A message the model can act on: the fields, not the whole dump."""
    messages = []
    for error in exc.errors()[:limit]:
        location = ".".join(str(part) for part in error["loc"])
        messages.append(f"{location}: {error['msg']}")
    remaining = len(exc.errors()) - limit
    if remaining > 0:
        messages.append(f"... and {remaining} more")
    return "; ".join(messages)
