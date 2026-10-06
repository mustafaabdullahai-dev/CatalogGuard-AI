"""Typed graph state for the agent orchestration graph (Phase 14, §24, §36).

§24 asks for a strongly typed state and, in the same breath, warns against an
uncontrolled giant one. So this module holds only what Phase 14's nodes read or
write. The keys §24 lists for work that has not happened — `compliance_findings`,
`risk_findings`, `simulation_result`, `approval_status`, `execution_result` —
are deliberately absent: they belong to deterministic services and to later
phases, and carrying them here would put fields the graph never touches into the
object the graph must serialize.

Two rules the shapes below exist to enforce:

  - **Important structured data is Pydantic (§36).** A node's output is parsed
    into one of these models or the node fails; free-form dicts do not travel
    between nodes, so no downstream node can be handed something shaped only by
    whatever the model felt like returning.
  - **Confidence carries its evidence (§37).** A proposal stores confidence
    *with* the evidence, the reason and the source that produced it, and
    `validation_status` starts at `pending` because nothing has been simulated
    or approved yet. Confidence alone decides nothing.

Nothing here is written to the database. §5 forbids the LLM from modifying
production catalog data: these are proposals the deterministic layer will accept
or reject later.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

from pydantic import BaseModel, Field

from app.models.tenancy import UserRole

AgentStatus = Literal["pending", "completed", "aborted"]

IssueSeverity = Literal["error", "warning"]
SourceType = Literal["file_upload", "marketplace_api", "manual_entry", "unknown"]


class IntakeIssue(BaseModel):
    """One problem intake found with the payload it was handed."""

    code: str = Field(description="Stable machine code, e.g. missing_identity_column")
    detail: str
    severity: IssueSeverity = "error"


class IntakeReport(BaseModel):
    """What the payload is, and whether it is fit to continue (§7 intake)."""

    source_type: SourceType
    record_count: int = Field(default=0, ge=0)
    classification: str = Field(default="", description="What kind of catalog data this is")
    issues: list[IntakeIssue] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        """No error-severity issue. A warning still proceeds, but is carried."""
        return not any(issue.severity == "error" for issue in self.issues)


class FieldMapping(BaseModel):
    """One proposed source-field → canonical-field mapping (§7 normalization).

    `needs_review` defaults to True: §7 says the mapping "must be validated", so
    a model-proposed equivalence is a candidate until a human or a deterministic
    check accepts it.
    """

    source_field: str
    canonical_field: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str
    needs_review: bool = True


class NormalizationPlan(BaseModel):
    """Proposed normalization for one payload (§7 normalization)."""

    mappings: list[FieldMapping] = Field(default_factory=list)
    unmapped_fields: list[str] = Field(default_factory=list)


class ProposedChange(BaseModel):
    """A proposal for one disagreement — machine-readable, never self-executing."""

    conflict_id: str
    field: str
    current_value: str | None = None
    proposed_value: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str
    reason: str
    source: str = Field(description="Which model and node produced this")
    validation_status: Literal["pending", "validated", "rejected"] = "pending"
    risk: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Filled by the deterministic risk engine (Phase 16), not by a model",
    )


class ReconciliationOutput(BaseModel):
    """The reconciliation node's whole answer: what it proposes and what it could not settle.

    Both halves are required deliberately. A model that returns only its
    proposals silently rewrites "unresolved" as "resolved", and the queue would
    never learn that a disagreement was left alone.
    """

    proposals: list[ProposedChange] = Field(default_factory=list)
    unresolved_conflicts: list[str] = Field(
        default_factory=list,
        description="Conflict ids the evidence did not settle — reported, not guessed",
    )


class AgentError(BaseModel):
    """A node that failed, recorded rather than swallowed (§67, D-007 #3)."""

    node: str
    code: str
    detail: str
    attempts: int = Field(default=0, ge=0, description="Completion attempts spent")
    tokens: int = Field(default=0, ge=0, description="Tokens the failed attempt cost")


class ProductObservation(BaseModel):
    """Read-only input: what one source said about one product."""

    external_id: str
    title: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class ConflictObservation(BaseModel):
    """Read-only input: a disagreement the deterministic layer already detected.

    The graph does not discover conflicts — §11 conflict detection is a Python
    service, and having a model find them would make the queue depend on which
    model happened to be serving that day.
    """

    conflict_id: str
    field: str
    candidates: list[str] = Field(min_length=2)
    evidence: str = ""


class Usage(BaseModel):
    """Token and call accounting for the graph run (§56, D-007 #5).

    Repairs are counted because a repair loop re-spends the same input: cost
    control that ignores retries under-reports the free tier's real burn.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    repairs: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def plus(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            calls=self.calls + other.calls,
            repairs=self.repairs + other.repairs,
        )


class CatalogState(TypedDict):
    """§24's shape, bounded to what Phase 14 owns.

    Required keys are the contract of *entering* the graph: who is asking and
    what was handed in. Everything the nodes produce is `NotRequired`, because a
    node that fails produces nothing — and a state where every output is
    required would force each node to invent a placeholder for work that never
    happened, which is exactly the kind of value that later reads as real.
    """

    workspace_id: str
    catalog_id: str
    # Who the tools are acting for. Required because a tool without a role
    # cannot be authorized (§23), and authorization is checked per run rather
    # than assumed from the fact that a graph is running.
    initiator_role: UserRole
    products: list[ProductObservation]
    conflicts: list[ConflictObservation]

    intake: NotRequired[IntakeReport]
    normalization: NotRequired[NormalizationPlan]
    proposed_changes: NotRequired[list[ProposedChange]]
    unresolved_conflicts: NotRequired[list[str]]
    errors: NotRequired[list[AgentError]]
    llm_usage: NotRequired[Usage]
    status: NotRequired[AgentStatus]
