"""The orchestration graph (Phase 14, §6, §7, §24).

LangGraph wires the §7 roles into one run:

    START → intake → normalize → analyze → finalize → END

with a conditional edge between each pair that sends the run straight to
`finalize` the moment a node records an error. That is D-007's "no fall-through"
expressed as topology rather than as a convention someone must remember: after a
failure the next node has no incoming edge from the current path.

`finalize` is where the run's status is decided, and it is Python — §6 requires
AI reasoning and deterministic operations to be distinguishable, and this graph
is written so you can read which is which without knowing what the model said.

Nothing here writes to the database. The graph returns proposals; §5 keeps the
LLM from touching production catalog data, and the deterministic services that
will accept or reject them arrive in later phases.
"""

from __future__ import annotations

from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from app.agents.client import ChatCompletionClient, StructuredLLM
from app.agents.nodes.core import (
    ANALYZE_NODE as ANALYZE,
)
from app.agents.nodes.core import (
    INTAKE_NODE as INTAKE,
)
from app.agents.nodes.core import (
    NORMALIZE_NODE as NORMALIZE,
)
from app.agents.nodes.core import (
    CatalogNodes,
    finalize,
    route,
)
from app.agents.state.models import CatalogState
from app.agents.tools import ToolRegistry, default_registry
from app.core.logging import get_logger

logger = get_logger(__name__)

FINALIZE = "finalize"


def build_graph(llm: StructuredLLM, *, tools: ToolRegistry | None = None) -> Any:
    """Compile the graph against `llm`, with `tools` available to the nodes.

    The client is injected rather than read from module state so a test can run
    the whole graph with scripted replies, and so a later phase can pass a client
    configured for one run without mutating global configuration. Tools are
    injected for the same reason, and each node receives only the subset it can
    use (`NODE_TOOLS`).
    """
    nodes = CatalogNodes(llm, tools=tools)
    builder = StateGraph(CatalogState)

    builder.add_node(INTAKE, nodes.intake)
    builder.add_node(NORMALIZE, nodes.normalize)
    builder.add_node(ANALYZE, nodes.analyze)
    builder.add_node(FINALIZE, finalize)

    builder.add_edge(START, INTAKE)
    builder.add_conditional_edges(INTAKE, route(NORMALIZE))
    builder.add_conditional_edges(NORMALIZE, route(ANALYZE))
    builder.add_conditional_edges(ANALYZE, route(FINALIZE))
    builder.add_edge(FINALIZE, END)

    return builder.compile()


async def run_graph(
    state: CatalogState,
    *,
    llm: StructuredLLM | None = None,
    tools: ToolRegistry | None = None,
) -> CatalogState:
    """Run one catalog analysis and return the resulting state.

    Raises nothing for a *node* failure: the run completes with `status=aborted`
    and an `errors` list, because a failed analysis is an answer. It raises only
    when the graph itself cannot start, which is a programming error.

    A client this function creates is closed before it returns: its transport is
    bound to the event loop that ran the graph, and a run's own resources should
    not outlive the run. A caller-supplied client stays open — the caller decides
    how long it lives.

    Tools default to the read-only registry (Phase 15). Pass `ToolRegistry()` for
    a run that must have none, rather than reaching in to remove them after.
    """
    owned: ChatCompletionClient | None = None
    if llm is None:
        owned = ChatCompletionClient()
        llm = owned
    registry = default_registry() if tools is None else tools
    try:
        result = await build_graph(llm, tools=registry).ainvoke(state)
    finally:
        if owned is not None:
            await owned.aclose()
    logger.info(
        "catalog_agent_run_finished",
        workspace_id=state["workspace_id"],
        status=result.get("status"),
        errors=len(result.get("errors") or []),
        usage=(result.get("llm_usage").total_tokens if result.get("llm_usage") else 0),
    )
    return cast(CatalogState, result)
