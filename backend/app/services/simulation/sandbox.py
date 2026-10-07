"""The sandbox: apply a proposal to a virtual catalog and report the impact
(Phase 17, §18, §19, §17).

§18 is the reason this exists — a change is never applied to production to see
what happens. The sandbox takes a snapshot and a proposal, produces a second
product value in memory, and runs the validation chain against it. Nothing here
touches a database, a queue or a clock: `simulate` reads its arguments and
returns a result, which is what makes the same proposal produce the same
report twice and what lets the whole chain be tested offline.

The chain is §18's, in order:

    relationship validation → schema validation → compliance validation
                            → SEO analysis → conflict detection → impact report

Two stages can honestly say "not run" and do. Relationship validation is
skipped when the snapshot carries no tree — a proposal about a flat catalog has
nothing to check — and compliance validation is skipped when no checker is
configured, because the corpus lives in a database D-006 still records as
unconfirmed. A `None` there means *not checked*; it never means *passed*. A
stage that has not run must not be able to approve anything.

What the result holds is what §19's diff view needs (before, proposed,
indicator, risk, approval) and what §17 asks for without promising (current,
proposed, expected impact, risk, recommendation — and no claim that search
results will or will not change).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, replace
from typing import Any, Literal, Protocol

from app.models.catalog import SourceType
from app.models.reconciliation import ChangeProposal, ProposalStatus, RiskLevel
from app.services.catalog.relationships import (
    RelationshipEdge,
    TreeNode,
    summarize,
    validate_relationships,
)
from app.services.reconciliation.conflicts import FieldObservation, detect_field_conflicts
from app.services.reconciliation.risk import SEO_SENSITIVE_FIELDS

StageStatus = Literal["passed", "failed", "flagged", "skipped"]

# Fields shown as context rows in the diff, so the table is a table rather than
# a single line. The changed field always comes first.
CONTEXT_FIELDS = ("title", "category_path", "parent_id", "sku")
_URL_FIELDS = frozenset({"url", "slug", "handle", "listing_url"})

# Product columns whose length the database actually enforces. The rules below
# are those limits, not SEO advice: a proposal that would violate one is a
# proposal that cannot be written.
_MAX = {
    "sku": 255,
    "mpn": 255,
    "brand": 255,
    "manufacturer": 255,
    "gtin": 64,
    "upc": 64,
}
_REQUIRED = frozenset({"sku"})
_IDENTIFIER_FIELDS = frozenset({"gtin", "upc"})
_NUMERIC_FIELDS = frozenset({"price", "cost", "weight", "length", "width", "height"})
_GS1_DIGITS = re.compile(r"^\d{8,14}$")


@dataclass(frozen=True, slots=True)
class ProductState:
    """One product as the sandbox models it — a value object, not an ORM row.

    Columns that exist on `products` are columns here; everything else lives in
    `attributes`, which is where the product model keeps the rest. Applying a
    change to an unknown name writes it as an attribute rather than failing, so
    a proposal about a field this class has never heard of still simulates.
    """

    id: str
    sku: str
    title: str | None = None
    description: str | None = None
    category_path: str | None = None
    gtin: str | None = None
    upc: str | None = None
    mpn: str | None = None
    brand: str | None = None
    manufacturer: str | None = None
    parent_id: str | None = None
    is_parent: bool = False
    attributes: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_product(cls, product: Any) -> ProductState:
        """Read the fields the sandbox needs off an ORM `Product`."""
        return cls(
            id=str(product.id),
            sku=product.sku,
            title=product.title,
            description=product.description,
            category_path=product.category_path,
            gtin=product.gtin,
            upc=product.upc,
            mpn=product.mpn,
            brand=product.brand,
            manufacturer=product.manufacturer,
            parent_id=str(product.parent_id) if product.parent_id is not None else None,
            is_parent=bool(product.is_parent),
            attributes=dict(product.attributes or {}),
        )

    @property
    def columns(self) -> frozenset[str]:
        return _COLUMNS

    def value_of(self, field_name: str) -> Any:
        if field_name in _COLUMNS:
            return getattr(self, field_name)
        return self.attributes.get(field_name)

    def applied(self, field_name: str, value: Any) -> ProductState:
        """The same product with one field set. Never mutates the original."""
        if field_name in _COLUMNS:
            return replace(self, **{field_name: value})
        attributes = dict(self.attributes)
        attributes[field_name] = value
        return replace(self, attributes=attributes)


_COLUMNS = frozenset(
    f.name for f in fields(ProductState) if f.name not in {"id", "attributes"}
)


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """Everything the sandbox may read.

    Supplied by the caller — the API layer loads it, tests write it — so the
    engine itself never reaches for a session. `nodes` and `edges` describe the
    relationship tree *before* the change; when they are empty the relationship
    stage reports `skipped` rather than inventing a tree it has not seen.
    """

    product: ProductState
    nodes: tuple[TreeNode, ...] = ()
    edges: tuple[RelationshipEdge, ...] = ()
    catalog_version: int = 1

    @property
    def version(self) -> int:
        return self.catalog_version


@dataclass(frozen=True, slots=True)
class ProposedChange:
    """The proposal, reduced to what simulation asks of it."""

    change_ref: str
    field_name: str
    current_value: Any = None
    proposed_value: Any = None
    evidence: tuple[dict[str, Any], ...] = ()
    affected_channels: tuple[str, ...] = ()
    affected_products: tuple[str, ...] = ()
    risk_level: RiskLevel = RiskLevel.LOW
    confidence: float = 1.0

    @classmethod
    def from_proposal(cls, proposal: ChangeProposal) -> ProposedChange:
        """The one place simulation touches the ORM."""
        return cls(
            change_ref=proposal.change_ref,
            field_name=proposal.field_name,
            current_value=proposal.current_value,
            proposed_value=proposal.proposed_value,
            evidence=tuple(
                dict(item) for item in (proposal.evidence or []) if isinstance(item, dict)
            ),
            affected_channels=tuple(proposal.affected_channels or ()),
            affected_products=(
                (str(proposal.product_id),) if proposal.product_id is not None else ()
            ),
            risk_level=proposal.risk_level,
            confidence=proposal.confidence,
        )


@dataclass(frozen=True, slots=True)
class StageOutcome:
    """One stage of §18's chain: what ran, what it found, what it decided."""

    name: str
    status: StageStatus
    findings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "findings": list(self.findings),
        }


@dataclass(frozen=True, slots=True)
class ComplianceOutcome:
    """The answer a compliance checker gives, including "not checked"."""

    valid: bool | None
    findings: tuple[str, ...] = ()
    detail: str = ""


class ComplianceCheck(Protocol):
    """A corpus-backed checker (§13). None is configured until a database is."""

    async def check(self, change: ProposedChange, after: ProductState) -> ComplianceOutcome: ...


NOT_CONFIGURED = ComplianceOutcome(
    valid=None,
    detail="no compliance checker configured; the corpus lives in a database this run cannot reach",
)


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """What the run produced: the flags, the report, and what to write back."""

    schema_valid: bool | None
    relationship_valid: bool | None
    compliance_valid: bool | None
    seo_sensitive: bool
    overall_risk: RiskLevel
    diff: dict[str, Any]
    impact_report: dict[str, Any]
    explanation: str
    stages: tuple[StageOutcome, ...]
    validation_errors: tuple[str, ...]
    validation_status: str  # "validated" | "rejected"
    proposal_status: ProposalStatus
    base_catalog_version: int

    @property
    def failed(self) -> bool:
        return self.validation_status == "rejected"


def validation_for(stages: list[StageOutcome]) -> tuple[str, ProposalStatus]:
    """Map stage outcomes onto the two columns a run writes back.

    Only a stage that actually ran may fail a proposal. `skipped` is not
    `passed`: a missing compliance corpus must never read as an approval.
    """
    if any(stage.status == "failed" for stage in stages):
        return "rejected", ProposalStatus.REJECTED_BY_VALIDATION
    return "validated", ProposalStatus.SIMULATED


async def simulate(
    change: ProposedChange,
    snapshot: CatalogSnapshot,
    *,
    compliance: ComplianceCheck | None = None,
) -> SimulationResult:
    """Run §18's chain over a virtual copy of the product.

    Pure apart from the optional checker: same change, same snapshot, same
    report. The product in `snapshot` is never modified — `applied` returns a
    second value object — which is the whole point of the exercise.
    """
    after = snapshot.product.applied(change.field_name, change.proposed_value)

    stages: list[StageOutcome] = []

    # §18 step 1 — relationship validation, on the tree as it would be.
    before_nodes, before_edges = list(snapshot.nodes), list(snapshot.edges)
    after_nodes, after_edges = _tree_after(snapshot, change, after)
    if not before_nodes:
        relationship_valid: bool | None = None
        stages.append(
            StageOutcome(
                "relationship_validation",
                "skipped",
                ("the snapshot carries no relationship tree, so there is nothing to validate",),
            )
        )
        before_summary: dict[str, int] = {}
        after_summary: dict[str, int] = {}
    else:
        before_findings = validate_relationships(before_nodes, before_edges)
        after_findings = validate_relationships(after_nodes, after_edges)
        before_summary, after_summary = summarize(before_findings), summarize(after_findings)
        introduced = [
            f for f in after_findings if f not in before_findings
        ]
        # Only what the change *introduced* may fail it. A tree that was
        # already broken is shown in the impact counts — and would sink every
        # proposal on the catalog if it could fail them.
        introduced_errors = [f for f in introduced if f.severity == "error"]
        relationship_valid = not introduced_errors
        stages.append(
            StageOutcome(
                "relationship_validation",
                "passed" if relationship_valid else "failed",
                tuple(sorted(f"{f.kind.value}: {f.detail}" for f in introduced)),
            )
        )

    # §18 step 2 — schema validation, on the field this change writes.
    findings, schema_checked = _schema_findings(
        change.field_name, after.value_of(change.field_name)
    )
    schema_findings = tuple(findings)
    schema_valid: bool | None = None if not schema_checked else not schema_findings
    stages.append(
        StageOutcome(
            "schema_validation",
            "skipped"
            if not schema_checked
            else ("passed" if schema_valid else "failed"),
            schema_findings
            or (
                ()
                if schema_checked
                else (f"no schema rule for {change.field_name}",)
            ),
        )
    )

    # §18 step 3 — compliance validation, only when something can answer.
    outcome = (
        await compliance.check(change, after) if compliance is not None else NOT_CONFIGURED
    )
    compliance_valid = outcome.valid
    compliance_status: StageStatus = (
        "skipped" if compliance_valid is None else ("passed" if compliance_valid else "failed")
    )
    stages.append(
        StageOutcome(
            "compliance_validation",
            compliance_status,
            outcome.findings or ((outcome.detail,) if outcome.detail else ()),
        )
    )

    # §18 step 4 — SEO analysis (§17). Flags, never promises.
    seo_findings = tuple(_seo_findings(change, after))
    seo_sensitive = bool(
        change.field_name in SEO_SENSITIVE_FIELDS or change.field_name in _URL_FIELDS
    )
    stages.append(
        StageOutcome(
            "seo_analysis",
            "flagged" if seo_findings else "passed",
            seo_findings,
        )
    )

    # §18 step 5 — conflict detection on the simulated state.
    observations = _observations(change, after)
    remaining = detect_field_conflicts(
        str(after.id),
        observations,
        affected_channels=list(change.affected_channels),
        affected_products=list(change.affected_products),
    )
    source_values = _source_values(change)
    proposed_value = after.value_of(change.field_name)
    disagreeing = sorted(
        source
        for source, value in source_values.items()
        if value != proposed_value
    )
    stages.append(
        StageOutcome(
            "conflict_detection",
            "passed" if not remaining else "flagged",
            tuple(
                f"{len(remaining)} disagreement(s) remain among the sources"
                if remaining
                else ()
            )
            + tuple(f"{source} still says {source_values[source]!r}" for source in disagreeing),
        )
    )

    validation_status, proposal_status = validation_for(stages)
    validation_errors = tuple(
        f"{stage.name}: {finding}" for stage in stages if stage.status == "failed"
        for finding in stage.findings
    )

    diff = _diff(change, snapshot, after, stages)
    impact = _impact_report(
        change,
        stages,
        remaining=len(remaining),
        disagreeing=disagreeing,
        before_summary=before_summary,
        after_summary=after_summary,
        relationship_valid=relationship_valid,
    )
    explanation = _explain(
        change,
        snapshot,
        stages=stages,
        validation_status=validation_status,
        remaining=len(remaining),
        disagreeing=disagreeing,
    )

    return SimulationResult(
        schema_valid=schema_valid,
        relationship_valid=relationship_valid,
        compliance_valid=compliance_valid,
        seo_sensitive=seo_sensitive,
        overall_risk=change.risk_level,
        diff=diff,
        impact_report=impact,
        explanation=explanation,
        stages=tuple(stages),
        validation_errors=validation_errors,
        validation_status=validation_status,
        proposal_status=proposal_status,
        base_catalog_version=snapshot.version,
    )


# --- stages ------------------------------------------------------------------


def _schema_findings(field_name: str, value: Any) -> tuple[list[str], bool]:
    """The shape rules a proposal must satisfy, from the model's own limits.

    Returns the findings and whether any rule applied. Fields with no rule are
    reported as *not checked* rather than as passing: `attributes` is an open
    bag by design (§25), and a stage that examined nothing must not be able to
    approve the thing it did not examine.
    """
    if (
        field_name not in _MAX
        and field_name not in _REQUIRED
        and field_name not in _NUMERIC_FIELDS
    ):
        return [], False

    empty = value is None or (isinstance(value, str) and not value.strip())
    if empty:
        if field_name in _REQUIRED:
            return [f"{field_name} would be empty, and a product without it cannot exist"], True
        return [], True

    findings: list[str] = []
    if (field_name in _REQUIRED or field_name in _MAX) and field_name not in _IDENTIFIER_FIELDS:
        if not isinstance(value, str):
            findings.append(f"{field_name} must be text, got {type(value).__name__}")
        else:
            limit = _MAX.get(field_name)
            if limit is not None and len(value) > limit:
                findings.append(
                    f"{field_name} is {len(value)} characters; the column holds {limit}"
                )
    if field_name in _IDENTIFIER_FIELDS:
        digits = re.sub(r"[\s-]", "", str(value))
        if not _GS1_DIGITS.match(digits):
            findings.append(
                f"{field_name} must be a GS1 identifier of 8 to 14 digits, got {value!r}"
            )
    if field_name in _NUMERIC_FIELDS:
        try:
            number = float(value)
        except (TypeError, ValueError):
            findings.append(f"{field_name} must be a number, got {value!r}")
        else:
            if number < 0:
                findings.append(f"{field_name} cannot be negative, got {value}")
    return findings, True


def _seo_findings(change: ProposedChange, after: ProductState) -> list[str]:
    """§17's concerns as observable facts about the change.

    No prediction of ranking, traffic or index position appears here: §17
    forbids claiming an SEO outcome, and a deterministic engine can only say
    what the change touches.
    """
    field_name = change.field_name
    findings: list[str] = []
    if field_name in SEO_SENSITIVE_FIELDS:
        findings.append(
            f"{field_name} is indexing-sensitive: how this product is discovered changes"
        )
    if field_name in _URL_FIELDS:
        findings.append(
            "the public URL changes, so links already pointing at it stop resolving"
        )
    value = after.value_of(field_name)
    if field_name == "title" and (value is None or not str(value).strip()):
        findings.append(
            "the product would have no title, and an untitled product cannot be listed"
        )
    if field_name in {"description", "meta_description"} and (
        value is None or not str(value).strip()
    ):
        findings.append("the description would be empty")
    return findings


def _source_values(change: ProposedChange) -> dict[str, object]:
    """What each source literally claimed, read back off the proposal's evidence.

    The proposal keeps the conflict's evidence, which carries `values`
    (source → value) and `source_records` (source → label and record id). That
    is enough to reconstruct the argument without going back to the source
    records — which matters, because the sandbox should be able to run against
    a snapshot alone.
    """
    values: dict[str, object] = {}
    for item in change.evidence:
        for source, value in (item.get("values") or {}).items():
            if isinstance(source, str):
                values[source] = value
    return values


def _observations(change: ProposedChange, after: ProductState) -> dict[str, list[FieldObservation]]:
    """The simulated state: every source's claim, plus the new canonical value."""
    labels: dict[str, str | None] = {}
    records: dict[str, dict[str, Any]] = {}
    for item in change.evidence:
        for record in item.get("source_records") or []:
            source = record.get("source")
            if isinstance(source, str):
                labels[source] = record.get("label")
                records[source] = dict(record)

    observations: list[FieldObservation] = []
    for source, value in _source_values(change).items():
        try:
            source_type = SourceType(source)
        except ValueError:
            continue
        record = records.get(source, {})
        observations.append(
            FieldObservation(
                source=source_type,
                value=value,
                source_label=labels.get(source),
                record_id=record.get("record_id"),
                is_current_canonical=bool(record.get("is_current_canonical")),
                updated_at=record.get("updated_at"),
            )
        )
    observations.append(
        FieldObservation(
            source=SourceType.INTERNAL,
            value=after.value_of(change.field_name),
            source_label="canonical",
            is_current_canonical=True,
        )
    )
    return {change.field_name: observations}


# --- diff and report ---------------------------------------------------------


def _tree_after(
    snapshot: CatalogSnapshot, change: ProposedChange, after: ProductState
) -> tuple[list[TreeNode], list[RelationshipEdge]]:
    """The relationship tree as it would be if the change were applied."""
    nodes: list[TreeNode] = []
    for node in snapshot.nodes:
        if str(node.id) == after.id:
            node = replace(
                node,
                sku=after.sku,
                title=after.title,
                parent_id=after.parent_id,
                is_parent=after.is_parent,
            )
        nodes.append(node)

    edges: list[RelationshipEdge] = []
    if change.field_name == "parent_id":
        # The product's own edge moves with it; a root has no edge at all.
        kept = [e for e in snapshot.edges if str(e.child_id) != after.id]
        previous = [e for e in snapshot.edges if str(e.child_id) == after.id]
        edges = kept
        if after.parent_id is not None:
            template = previous[0] if previous else RelationshipEdge(
                parent_id=after.parent_id, child_id=after.id
            )
            edges.append(replace(template, parent_id=after.parent_id, child_id=after.id))
    else:
        edges = list(snapshot.edges)
    return nodes, edges


def _children(snapshot: CatalogSnapshot, parent_id: str | None) -> list[str]:
    """SKUs of the products hanging off `parent_id`, before or after."""
    if parent_id is None:
        return []
    skus = {
        str(node.id): node.sku or str(node.id)
        for node in snapshot.nodes
    }
    ids = [str(node.id) for node in snapshot.nodes if str(node.parent_id or "") == parent_id]
    return sorted(skus[i] for i in ids)


def _diff(
    change: ProposedChange,
    snapshot: CatalogSnapshot,
    after: ProductState,
    stages: list[StageOutcome],
) -> dict[str, Any]:
    """§19's table, plus §18's before/after tree."""
    risky = change.risk_level.rank >= RiskLevel.HIGH.rank
    rows: list[dict[str, Any]] = []
    for field_name in dict.fromkeys((change.field_name, *CONTEXT_FIELDS)):
        current = snapshot.product.value_of(field_name)
        proposed = after.value_of(field_name)
        if field_name == change.field_name:
            if current == proposed:
                indicator = "unchanged"
            elif current is None:
                indicator = "added"
            elif proposed is None:
                indicator = "removed"
            else:
                indicator = "modified"
        else:
            proposed = current
            indicator = "unchanged"
        rows.append(
            {
                "field": field_name,
                "current": current,
                "proposed": proposed,
                "indicator": indicator,
                "risky": risky and field_name == change.field_name,
                "requires_approval": field_name == change.field_name,
            }
        )

    return {
        "rows": rows,
        "tree": {
            "before": {
                "product": snapshot.product.sku,
                "parent": snapshot.product.parent_id,
                "children": _children(snapshot, snapshot.product.id),
            },
            "after": {
                "product": after.sku,
                "parent": after.parent_id,
                "children": _children(snapshot, after.id),
            },
        },
        "stages": [
            {"name": stage.name, "status": stage.status, "findings": list(stage.findings)}
            for stage in stages
        ],
    }


def _impact_report(
    change: ProposedChange,
    stages: list[StageOutcome],
    *,
    remaining: int,
    disagreeing: list[str],
    before_summary: dict[str, int],
    after_summary: dict[str, int],
    relationship_valid: bool | None,
) -> dict[str, Any]:
    """The report §18 ends with and §17 orders: impact, risk, recommendation."""
    failed = any(stage.status == "failed" for stage in stages)
    if failed:
        recommendation = "fix the findings above before this change can be simulated again"
    elif change.risk_level.rank >= RiskLevel.HIGH.rank:
        recommendation = (
            "a human must approve this change explicitly; it is risk-scored "
            f"{change.risk_level.value}"
        )
    else:
        recommendation = "ready for review; no stage failed"
    return {
        "affected_channels": list(change.affected_channels),
        "affected_products": max(len(change.affected_products), 1),
        "relationship_findings": {"before": before_summary, "after": after_summary},
        "relationship_valid": relationship_valid,
        "conflicts_remaining": remaining,
        "sources_still_disagreeing": disagreeing,
        "risk": {
            "level": change.risk_level.value,
            "requires_approval": change.risk_level.requires_explicit_approval,
        },
        "recommendation": recommendation,
    }


def _explain(
    change: ProposedChange,
    snapshot: CatalogSnapshot,
    *,
    stages: list[StageOutcome],
    validation_status: str,
    remaining: int,
    disagreeing: list[str],
) -> str:
    """Plain language, and no promised outcome (§18, §17, §67)."""
    def render(value: Any) -> str:
        if value is None or value == "":
            return "nothing"
        return repr(value) if not isinstance(value, str) else f"'{value}'"

    failed = [stage for stage in stages if stage.status == "failed"]
    skipped = [stage for stage in stages if stage.status == "skipped"]

    parts = [
        f"Changing {change.field_name} on {snapshot.product.sku} from "
        f"{render(change.current_value)} to {render(change.proposed_value)}."
    ]
    if failed:
        parts.append(
            "Fails " + " and ".join(stage.name for stage in failed) + "; "
            + " ".join(f"({finding})" for stage in failed for finding in stage.findings)
        )
    else:
        parts.append("Every stage that ran passed.")
    if skipped:
        parts.append("Not checked: " + ", ".join(stage.name for stage in skipped) + ".")
    if remaining:
        parts.append(
            f"{remaining} disagreement(s) remain after the change"
            + (f" ({', '.join(disagreeing)} still say otherwise)" if disagreeing else "")
            + ", so a later reconciliation pass will see them again."
        )
    if validation_status == "validated":
        parts.append(
            f"Simulated at catalog version {snapshot.version}; risk is "
            f"{change.risk_level.value}."
        )
    return " ".join(parts)
