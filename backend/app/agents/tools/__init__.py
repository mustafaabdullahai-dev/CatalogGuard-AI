"""Agent tools (Phase 15, §23).

`ToolRegistry` is the contract; `catalog` holds the read-only adapters over the
deterministic services; `spec` holds the rules both follow — validate in,
validate out, never raise at the model, log metadata only, check authorization
against the same matrix the API uses.
"""

from app.agents.tools.catalog import (
    CHANGE_RISK,
    DEFAULT_TOOLS,
    IDENTITY_MATCH,
    NORMALIZE_FIELD,
    SOURCE_AUTHORITY,
)
from app.agents.tools.spec import (
    DEFAULT_MAX_OUTPUT_CHARS,
    TOOL_OUTPUT_POLICY,
    ToolCall,
    ToolCallOutcome,
    ToolContext,
    ToolRegistry,
    ToolSpec,
    wrap_tool_result,
)
from app.core.config import get_settings


def default_registry(*, max_output_chars: int | None = None) -> ToolRegistry:
    """Every read-only tool this phase ships.

    Sized from configuration so the budget a tool's output is held to is
    operator-visible rather than buried in a constant.
    """
    if max_output_chars is None:
        max_output_chars = get_settings().tool_output_max_chars
    return ToolRegistry(DEFAULT_TOOLS, max_output_chars=max_output_chars)


__all__ = [
    "CHANGE_RISK",
    "DEFAULT_MAX_OUTPUT_CHARS",
    "DEFAULT_TOOLS",
    "IDENTITY_MATCH",
    "NORMALIZE_FIELD",
    "SOURCE_AUTHORITY",
    "TOOL_OUTPUT_POLICY",
    "ToolCall",
    "ToolCallOutcome",
    "ToolContext",
    "ToolRegistry",
    "ToolSpec",
    "default_registry",
    "wrap_tool_result",
]
