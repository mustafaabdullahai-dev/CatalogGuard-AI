"""Graph nodes — the §7 roles, one callable each (Phase 14, §6, §7, §36).

Each node asks the client for **one validated object** and returns the keys it
produced. Three properties are deliberate:

  - **No node touches the database, the queue or the filesystem.** §5 keeps the
    LLM away from production catalog data; Phase 15 gives nodes tools to call
    through instead, and Phase 18 gives them an approval path.
  - **Failure is recorded, not recovered from.** A node that cannot produce a
    validated object appends an `AgentError` and nothing else, and the router
    stops the run (D-007 #3: no fall-through).
  - **Not every node is a model.** `finalize` is plain Python — §6 requires the
    architecture to distinguish AI reasoning from deterministic operations, and
    deciding whether a run succeeded is the clearest place to draw that line.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agents.client import ModelTier, StructuredLLM, StructuredOutputError
from app.agents.state.models import (
    AgentError,
    CatalogState,
    IntakeReport,
    NormalizationPlan,
    ReconciliationOutput,
    Usage,
)
from app.agents.tools import ToolContext, ToolRegistry

MAX_LISTED_FIELDS = 40

INTAKE_NODE = "intake"
NORMALIZE_NODE = "normalize"
ANALYZE_NODE = "analyze"

# Which tools each node may reach (§7: specialized roles get specialized
# reach). The graph advertises a tool only to the node that can use its answer,
# so a model is never asked to consider a tool that has nothing to do with the
# question it is answering.
NODE_TOOLS: dict[str, tuple[str, ...]] = {
    INTAKE_NODE: (),
    NORMALIZE_NODE: ("normalize_field",),
    ANALYZE_NODE: ("resolve_source_authority", "resolve_identity", "assess_change_risk"),
}

INTAKE_ROLE = (
    "You are the catalog intake agent. You identify the source of a payload, "
    "classify what it contains, and report problems with it. You describe "
    "problems; you do not fix them and you do not invent rows that are not in "
    "the payload."
)

NORMALIZATION_ROLE = (
    "You are the catalog normalization agent. Propose mappings from source field "
    "names to canonical field names. Colour, color, product_color and variant_color "
    "may all map to color — but a mapping is a proposal. Set needs_review when the "
    "equivalence is a guess rather than an exact match or a documented synonym."
)

RECONCILIATION_ROLE = (
    "You are the catalog reconciliation agent. For each disagreement, propose the "
    "value that should become the catalog's truth, with the evidence that supports "
    "it. You propose; a deterministic engine and a human decide. Confidence "
    "reflects how strong the evidence is and never expresses certainty — if the "
    "evidence does not settle it, leave the conflict unresolved rather than guessing."
)


def _errors(state: CatalogState) -> list[AgentError]:
    return list(state.get("errors") or [])


def _usage(state: CatalogState) -> Usage:
    return state.get("llm_usage") or Usage()


def _catalog_summary(state: CatalogState) -> str:
    """A bounded description of the payload: enough to classify, cheap to send.

    Every token spent re-reading a whole catalog is a token the free tier counts
    (§56), and intake only needs shape — counts and field names — not values.
    """
    products = state["products"]
    conflicts = state["conflicts"]
    lines = [
        f"Workspace: {state['workspace_id']}",
        f"Catalog: {state['catalog_id']}",
        f"Products supplied: {len(products)}",
        f"Conflicts supplied: {len(conflicts)}",
    ]
    if products:
        keys = sorted({key for product in products for key in product.attributes})
        lines.append("Attribute keys seen: " + ", ".join(keys[:MAX_LISTED_FIELDS]))
        lines.append(f"First product external id: {products[0].external_id}")
    if conflicts:
        fields = sorted({conflict.field for conflict in conflicts})
        lines.append("Disagreed fields: " + ", ".join(fields[:MAX_LISTED_FIELDS]))
    return "\n".join(lines)


class CatalogNodes:
    """The §7 agent roles, bound to one LLM client.

    Tools are optional and per node: with none supplied the graph behaves as
    Phase 14 left it, and the `Completer` seam keeps both cases testable without
    a network.
    """

    def __init__(self, llm: StructuredLLM, *, tools: ToolRegistry | None = None) -> None:
        self._llm = llm
        self._tools: dict[str, ToolRegistry | None] = {}
        for node, names in NODE_TOOLS.items():
            self._tools[node] = tools.subset(names) if (tools and names) else None

    def _tool_kwargs(self, node: str, state: CatalogState) -> dict[str, Any]:
        """Tools and the caller they run as, or nothing at all.

        A tool without a role cannot be authorized, so the context is built from
        the state the run entered with — workspace and initiator — rather than
        defaulted, and a node with no tools passes no context either.
        """
        registry = self._tools.get(node)
        if registry is None:
            return {}
        return {
            "tools": registry,
            "tool_context": ToolContext(
                workspace_id=state["workspace_id"],
                role=state["initiator_role"],
            ),
        }

    async def intake(self, state: CatalogState) -> dict[str, Any]:
        """Identify the source, classify the data, report what is wrong (§7)."""
        try:
            report, usage = await self._llm.complete(
                tier=ModelTier.FAST,
                schema=IntakeReport,
                system=INTAKE_ROLE,
                user=_catalog_summary(state),
                **self._tool_kwargs(INTAKE_NODE, state),
            )
        except StructuredOutputError as exc:
            return {
                "errors": _errors(state) + [_error("intake", exc)],
                "llm_usage": _usage(state).plus(_usage_of(exc)),
            }

        errors = _errors(state)
        if not report.valid:
            errors = errors + [
                AgentError(
                    node="intake",
                    code="invalid_input",
                    detail="; ".join(
                        f"{issue.code}: {issue.detail}"
                        for issue in report.issues
                        if issue.severity == "error"
                    )
                    or "payload rejected by intake",
                )
            ]
        return {"intake": report, "errors": errors, "llm_usage": _usage(state).plus(usage)}

    async def normalize(self, state: CatalogState) -> dict[str, Any]:
        """Propose field mappings. The proposal is validated, never applied (§7)."""
        try:
            plan, usage = await self._llm.complete(
                tier=ModelTier.FAST,
                schema=NormalizationPlan,
                system=NORMALIZATION_ROLE,
                user=_catalog_summary(state),
                **self._tool_kwargs(NORMALIZE_NODE, state),
            )
        except StructuredOutputError as exc:
            return {
                "errors": _errors(state) + [_error("normalize", exc)],
                "llm_usage": _usage(state).plus(_usage_of(exc)),
            }
        return {
            "normalization": plan,
            "llm_usage": _usage(state).plus(usage),
        }

    async def analyze(self, state: CatalogState) -> dict[str, Any]:
        """Propose values for disagreements the deterministic layer found (§7)."""
        try:
            result, usage = await self._llm.complete(
                tier=ModelTier.REASONING,
                schema=ReconciliationOutput,
                system=RECONCILIATION_ROLE,
                user=_reconciliation_prompt(state),
                **self._tool_kwargs(ANALYZE_NODE, state),
            )
        except StructuredOutputError as exc:
            return {
                "errors": _errors(state) + [_error("analyze", exc)],
                "llm_usage": _usage(state).plus(_usage_of(exc)),
            }
        return {
            "proposed_changes": result.proposals,
            "unresolved_conflicts": result.unresolved_conflicts,
            "llm_usage": _usage(state).plus(usage),
        }


def _reconciliation_prompt(state: CatalogState) -> str:
    """The disagreements, with what each source actually said (§7, §11)."""
    lines = [_catalog_summary(state)]
    plan = state.get("normalization")
    if plan is not None and plan.mappings:
        lines.append("Proposed field mappings:")
        for mapping in plan.mappings[:MAX_LISTED_FIELDS]:
            lines.append(
                f"- {mapping.source_field} -> {mapping.canonical_field} "
                f"(confidence {mapping.confidence:.2f})"
            )
    lines.append("Disagreements to resolve:")
    for conflict in state["conflicts"]:
        lines.append(
            f"- {conflict.conflict_id} | {conflict.field} | candidates: "
            f"{' | '.join(conflict.candidates)}"
            + (f" | evidence: {conflict.evidence}" if conflict.evidence else "")
        )
    return "\n".join(lines)


async def finalize(state: CatalogState) -> dict[str, Any]:
    """Decide how the run ended — pure Python, no model (§6)."""
    errors = _errors(state)
    if errors:
        return {"status": "aborted"}
    return {"status": "completed"}


def _error(node: str, exc: StructuredOutputError) -> AgentError:
    """Record a failed node with what it cost before it failed (§67, §56)."""
    return AgentError(
        node=node,
        code="structured_output_failed",
        detail=str(exc),
        attempts=exc.attempts,
        tokens=exc.input_tokens + exc.output_tokens,
    )


def _usage_of(exc: StructuredOutputError) -> Usage:
    return Usage(
        input_tokens=exc.input_tokens,
        output_tokens=exc.output_tokens,
        calls=exc.attempts,
        repairs=max(0, exc.attempts - 1),
    )


def route(errors_after: str) -> Callable[[CatalogState], str]:
    """A router that stops the run the moment a node has recorded an error.

    D-007 #3 forbids falling through to later stages after a failure, and a
    conditional edge is where that becomes structural rather than a habit: the
    next node is unreachable, not merely skipped by convention.
    """

    def _route(state: CatalogState) -> str:
        if _errors(state):
            return "finalize"
        return errors_after

    return _route


__all__ = [
    "ANALYZE_NODE",
    "CatalogNodes",
    "INTAKE_NODE",
    "NODE_TOOLS",
    "NORMALIZE_NODE",
    "finalize",
    "route",
    "INTAKE_ROLE",
    "NORMALIZATION_ROLE",
    "RECONCILIATION_ROLE",
]
