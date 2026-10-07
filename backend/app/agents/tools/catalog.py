"""Read-only tools over the deterministic catalog services (Phase 15, §5, §23).

Four services, four adapters, no behaviour of their own beyond translation:
argument models the model can be held to, result models bounded so a tool
cannot flood the context, and dataclasses on the way in and out. Each handler
is a pure function of its arguments — none of these tools reads the database,
so they are as verifiable as the services they wrap.

Which tools exist here is deliberate. §23 lists eighteen; the ones that would
*create* a proposal, approve a change or execute a listing belong to Phases 16
to 20 and to the approval path §38 draws, and §5 keeps those out of the model's
hands. The lookups that need a repository (`catalog_search`, `product_lookup`,
`compliance_search`) need a live database, which D-006 still records as
unconfirmed. What is left is the read-only middle: normalization, authority,
identity and risk, all pure Python.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.agents.tools.spec import ToolArgs, ToolContext, ToolSpec
from app.models.catalog import SourceType
from app.models.reconciliation import RiskLevel
from app.services.catalog.authority import (
    AuthorityDecision,
    Candidate,
)
from app.services.catalog.authority import (
    resolve as resolve_authority,
)
from app.services.catalog.identity import (
    IdentityCandidate,
    resolve_identity,
)
from app.services.catalog.normalization import normalize_field
from app.services.reconciliation.risk import assess as assess_risk
from app.services.reconciliation.risk import context_from_change

MAX_EVIDENCE = 20


class NormalizeFieldArgs(ToolArgs):
    """One source column, as it appears in a payload."""

    raw_name: str = Field(min_length=1, max_length=200, description="Column name as supplied")
    value: str | int | float | bool | None = Field(
        default=None, description="A sample value from that column, if there is one"
    )


class NormalizeFieldResult(BaseModel):
    canonical: str | None = Field(
        default=None, description="Canonical field name, when it resolved"
    )
    resolved: bool
    normalized_value: str | None = Field(default=None, max_length=500)
    rules: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Rules that fired, as `field:rule` — the audit trail of the mapping",
    )


def _normalize_field(args: NormalizeFieldArgs, _context: ToolContext) -> NormalizeFieldResult:
    resolution = normalize_field(args.raw_name, args.value)
    return NormalizeFieldResult(
        canonical=resolution.canonical,
        resolved=resolution.resolved,
        normalized_value=None if resolution.value is None else str(resolution.value)[:500],
        rules=[f"{rule.field}:{rule.rule}" for rule in resolution.rules[:10]],
    )


class AuthorityCandidateArgs(ToolArgs):
    source: SourceType
    value: str | int | float | bool | None
    source_label: str | None = Field(default=None, max_length=200)


class SourceAuthorityArgs(ToolArgs):
    field_name: str = Field(min_length=1, max_length=100)
    candidates: list[AuthorityCandidateArgs] = Field(min_length=1, max_length=10)


class SourceAuthorityResult(BaseModel):
    field_name: str
    decision: AuthorityDecision
    winner_source: str | None = None
    winner_value: str | int | float | bool | None = None
    competing_values: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    reason: str = Field(max_length=500)
    needs_escalation: bool


def _resolve_source_authority(
    args: SourceAuthorityArgs, _context: ToolContext
) -> SourceAuthorityResult:
    resolution = resolve_authority(
        args.field_name,
        [
            Candidate(
                source=candidate.source,
                value=candidate.value,
                source_label=candidate.source_label,
            )
            for candidate in args.candidates
        ],
    )
    return SourceAuthorityResult(
        field_name=resolution.field_name,
        decision=resolution.decision,
        winner_source=None if resolution.winner is None else resolution.winner.source.value,
        winner_value=None if resolution.winner is None else resolution.winner.value,
        competing_values=resolution.competing_values,
        reason=resolution.reason[:500],
        needs_escalation=resolution.needs_escalation,
    )


class IdentityCandidateArgs(ToolArgs):
    product_id: str | None = Field(default=None, max_length=64)
    sku: str | None = Field(default=None, max_length=120)
    gtin: str | None = Field(default=None, max_length=64)
    upc: str | None = Field(default=None, max_length=64)
    mpn: str | None = Field(default=None, max_length=120)
    brand: str | None = Field(default=None, max_length=200)
    manufacturer: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=500)
    source: SourceType = SourceType.MARKETPLACE

    def to_candidate(self) -> IdentityCandidate:
        return IdentityCandidate(
            product_id=self.product_id,
            sku=self.sku,
            gtin=self.gtin,
            upc=self.upc,
            mpn=self.mpn,
            brand=self.brand,
            manufacturer=self.manufacturer,
            title=self.title,
            source=self.source,
        )


class IdentityMatchArgs(ToolArgs):
    incoming: IdentityCandidateArgs
    existing: list[IdentityCandidateArgs] = Field(min_length=1, max_length=20)


class IdentityEvidenceResult(BaseModel):
    kind: str = Field(max_length=80)
    detail: str = Field(max_length=300)
    weight: float


class IdentityMatchResult(BaseModel):
    match: bool
    confidence: float = Field(ge=0.0, le=1.0)
    kind: str = Field(
        max_length=40,
        description="How the match was reached, and so what may be done with it",
    )
    reason: str = Field(max_length=500)
    evidence: list[IdentityEvidenceResult] = Field(default_factory=list, max_length=MAX_EVIDENCE)
    requires_approval: bool
    proposed_product_id: str | None = Field(default=None, max_length=64)


def _resolve_identity(args: IdentityMatchArgs, _context: ToolContext) -> IdentityMatchResult:
    match = resolve_identity(
        args.incoming.to_candidate(),
        [candidate.to_candidate() for candidate in args.existing],
    )
    return IdentityMatchResult(
        match=match.match,
        confidence=round(match.confidence, 4),
        kind=match.kind.value,
        reason=match.reason[:500],
        evidence=[
            IdentityEvidenceResult(kind=item.kind, detail=item.detail[:300], weight=item.weight)
            for item in match.evidence[:MAX_EVIDENCE]
        ],
        requires_approval=match.requires_approval,
        proposed_product_id=match.proposed_product_id,
    )


class ChangeRiskArgs(ToolArgs):
    """One proposed change, described the way the risk engine describes it (§16)."""

    field_name: str = Field(min_length=1, max_length=100)
    current_value: str | int | float | bool | None = None
    proposed_value: str | int | float | bool | None = None
    affected_channels: list[str] = Field(default_factory=list, max_length=20)
    affected_product_ids: list[str] = Field(default_factory=list, max_length=50)
    relationship_types: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    compliance_violation_count: int = Field(default=0, ge=0, le=100)
    has_sales_history: bool = False
    estimated_daily_units: float = Field(default=0.0, ge=0.0, le=1_000_000_000)
    monthly_revenue_at_risk: str | None = Field(
        default=None,
        pattern=r"^\d{1,12}(\.\d{1,2})?$",
        description="Decimal amount as a string, so no float rounding reaches the score",
    )
    inventory_units_affected: int = Field(
        default=0,
        ge=0,
        le=1_000_000_000,
        description="Units of stock this change moves, when the caller knows them",
    )


class RiskFactorResult(BaseModel):
    code: str = Field(max_length=60)
    weight: int
    detail: str = Field(max_length=300)


class ChangeRiskResult(BaseModel):
    level: RiskLevel
    numeric_score: int
    requires_approval: bool
    # Wide enough to hold every code in `WEIGHTS` (guarded by a test): a
    # truncated factor list is a total nobody can re-add.
    factors: list[RiskFactorResult] = Field(default_factory=list, max_length=30)
    escalated_reason: str | None = Field(default=None, max_length=300)


def _assess_change_risk(args: ChangeRiskArgs, _context: ToolContext) -> ChangeRiskResult:
    revenue = Decimal(args.monthly_revenue_at_risk or "0")
    change = context_from_change(
        field_name=args.field_name,
        current_value=args.current_value,
        proposed_value=args.proposed_value,
        affected_channels=args.affected_channels,
        affected_product_ids=args.affected_product_ids,
        relationship_types=set(args.relationship_types),
        confidence=args.confidence,
        compliance_violation_count=args.compliance_violation_count,
        has_sales_history=args.has_sales_history,
        estimated_daily_units=args.estimated_daily_units,
        monthly_revenue_at_risk=revenue,
        inventory_units_affected=args.inventory_units_affected,
    )
    result = assess_risk(change)
    return ChangeRiskResult(
        level=result.level,
        numeric_score=result.numeric_score,
        requires_approval=result.requires_approval,
        factors=[
            RiskFactorResult(code=factor.code, weight=factor.weight, detail=factor.detail[:300])
            for factor in result.factors[:30]
        ],
        escalated_reason=None if result.escalated_reason is None else result.escalated_reason[:300],
    )


NORMALIZE_FIELD = ToolSpec(
    name="normalize_field",
    description=(
        "Map one source column name to its canonical field name, with the rules "
        "that fired. Use before proposing a mapping so the proposal cites a rule "
        "rather than a resemblance."
    ),
    args_model=NormalizeFieldArgs,
    result_model=NormalizeFieldResult,
    handler=_normalize_field,
)

SOURCE_AUTHORITY = ToolSpec(
    name="resolve_source_authority",
    description=(
        "Decide which source is authoritative for one field, or decline to. "
        "Returns tied and no-authoritative-source as answers: escalation is a "
        "result, not a failure."
    ),
    args_model=SourceAuthorityArgs,
    result_model=SourceAuthorityResult,
    handler=_resolve_source_authority,
)

IDENTITY_MATCH = ToolSpec(
    name="resolve_identity",
    description=(
        "Test whether one incoming record is the same product as any of the "
        "existing candidates, with the evidence and the kind of match. A "
        "non-match is a normal answer."
    ),
    args_model=IdentityMatchArgs,
    result_model=IdentityMatchResult,
    handler=_resolve_identity,
)

CHANGE_RISK = ToolSpec(
    name="assess_change_risk",
    description=(
        "Score one proposed change with the deterministic risk engine and "
        "return the band, the score and the factors that produced them. Pure "
        "function: same change, same score."
    ),
    args_model=ChangeRiskArgs,
    result_model=ChangeRiskResult,
    handler=_assess_change_risk,
)

DEFAULT_TOOLS: tuple[ToolSpec, ...] = (
    NORMALIZE_FIELD,
    SOURCE_AUTHORITY,
    IDENTITY_MATCH,
    CHANGE_RISK,
)
