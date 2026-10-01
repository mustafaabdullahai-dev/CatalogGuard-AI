"""Deterministic risk scoring (§16, D-016).

Every factor contributes a fixed weight, the weights sum, and the band comes
from the total. §16 forbids the LLM from deciding final risk, so nothing in
this module calls a model, and the whole calculation is reproducible from the
change proposal alone — a fact the approval screen states explicitly, because
"the AI says this is risky" is not an answer anyone can check.

Why weights rather than a model
    A merchant can read a table of factors and predict the band. A trained
    classifier cannot be argued with, and an unarguable risk score is a risk
    score nobody trusts. Weights are auditable, tunable per workspace, and
    versioned in code.

The floor rule
    Bands have a minimum. A GTIN change is CRITICAL even if the arithmetic says
    LOW, because the factors are a floor and not a ceiling: they cannot talk the
    engine down from a consequence that is irreversible regardless of context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.models.catalog import RelationshipType
from app.models.reconciliation import RiskLevel


@dataclass(frozen=True, slots=True)
class RiskFactor:
    """One reason a change is risky, with the weight it contributed."""

    code: str
    weight: int
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "weight": self.weight, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class RiskContext:
    """Everything the engine is allowed to look at.

    Deliberately a flat value object: if the score cannot be recomputed from
    these fields, the engine has taken an input it cannot explain.
    """

    field_name: str
    is_identity_field: bool = False
    is_gtin_change: bool = False
    is_sku_change: bool = False
    affects_parent_child: bool = False
    affects_variants: bool = False
    affects_category: bool = False
    affects_seo_field: bool = False
    affects_compliance: bool = False
    affects_marketplace_listing: bool = False
    affects_url_or_slug: bool = False
    is_deletion: bool = False
    is_restriction: bool = False
    relationship_types: frozenset[str] = frozenset()
    affected_channel_count: int = 0
    affected_product_count: int = 1
    has_sales_history: bool = False
    estimated_daily_units: float = 0.0
    monthly_revenue_at_risk: Decimal = Decimal(0)
    compliance_violation_count: int = 0
    evidence_count: int = 0
    confidence: float = 1.0


# Factor weights. Deliberately round and deliberately visible.
WEIGHTS: dict[str, int] = {
    # Identity. These are the changes from which a mistake is unrecoverable:
    # a merged product or a duplicated GTIN cannot be undone by editing the row.
    "gtin_modification": 45,
    "sku_modification": 30,
    "identity_mismatch": 25,
    "product_deletion": 40,
    # Structure.
    "parent_child_modification": 25,
    "variant_modification": 20,
    "category_modification": 15,
    "broken_relationship": 20,
    # Distribution. A change that reaches customers is worse than one that does not.
    "marketplace_listing": 15,
    "channel_breadth": 5,
    "url_or_slug_change": 20,
    # Discoverability.
    "seo_sensitive_field": 15,
    "title_change": 12,
    "description_change": 5,
    # Constraint.
    "compliance_violation": 40,
    "restriction_applied": 15,
    # Blast radius.
    "sales_impact": 20,
    "revenue_at_risk": 15,
    "bulk_change": 15,
    # Confidence in the change itself: a change nobody can justify is risky
    # because it is unexplainable to the person who has to approve it.
    "low_confidence": 15,
    "thin_evidence": 10,
}

# Band boundaries over the summed score.
THRESHOLDS: tuple[tuple[int, RiskLevel], ...] = (
    (75, RiskLevel.CRITICAL),
    (45, RiskLevel.HIGH),
    (20, RiskLevel.MEDIUM),
    (0, RiskLevel.LOW),
)

# Fields where a change is guaranteed to move search behaviour or identity.
SEO_SENSITIVE_FIELDS: frozenset[str] = frozenset(
    {"title", "handle", "slug", "url", "listing_url", "category_path", "keywords", "brand"}
)
IDENTITY_FIELDS: frozenset[str] = frozenset({"gtin", "upc", "mpn", "sku"})
DELETION_FIELDS: frozenset[str] = frozenset({"deleted", "_deleted", "status"})
RESTRICTION_FIELDS: frozenset[str] = frozenset(
    {"restricted", "restricted_reason", "compliance_status", "suppressed"}
)
TITLE_FIELDS: frozenset[str] = frozenset({"title", "product_title", "name"})
DESCRIPTION_FIELDS: frozenset[str] = frozenset({"description", "long_description"})
# Relationship types whose modification reshapes the product tree.
STRUCTURAL_RELATIONSHIPS: frozenset[str] = frozenset(
    {
        RelationshipType.PARENT.value,
        RelationshipType.VARIANT.value,
        RelationshipType.REPLACEMENT.value,
    }
)
# Channel and product counts above which a change is a bulk operation.
BULK_CHANNEL_THRESHOLD = 3
BULK_PRODUCT_THRESHOLD = 25
HIGH_DAILY_UNITS = 50.0
HIGH_REVENUE_AT_RISK = Decimal("10000")
THIN_EVIDENCE_THRESHOLD = 2
LOW_CONFIDENCE_THRESHOLD = 0.7

# Consequences that override the arithmetic.
FLOORS: tuple[tuple[str, RiskLevel], ...] = (
    # A GTIN that no longer matches its physical product is a compliance and
    # logistics failure that stays wrong until someone notices downstream.
    ("gtin_modification", RiskLevel.HIGH),
    # Removing a product or breaking a relationship is not a "field update".
    ("product_deletion", RiskLevel.HIGH),
    ("broken_relationship", RiskLevel.HIGH),
    ("compliance_violation", RiskLevel.CRITICAL),
    ("identity_mismatch", RiskLevel.HIGH),
)


@dataclass(frozen=True, slots=True)
class RiskResult:
    """The scored risk, with everything needed to challenge it."""

    level: RiskLevel
    numeric_score: int
    factors: tuple[RiskFactor, ...] = field(default_factory=tuple)
    escalated_reason: str | None = None

    @property
    def requires_approval(self) -> bool:
        return self.level.requires_explicit_approval

    def as_dict(self) -> dict[str, object]:
        return {
            "level": self.level.value,
            "numeric_score": self.numeric_score,
            "requires_approval": self.requires_approval,
            "factors": [f.as_dict() for f in self.factors],
            "escalated_reason": self.escalated_reason,
        }


def classify(score: int) -> RiskLevel:
    """Map a summed score onto a band."""
    for threshold, level in THRESHOLDS:
        if score >= threshold:
            return level
    return RiskLevel.LOW


def assess(context: RiskContext) -> RiskResult:
    """Score a proposed change.

    Pure function of its arguments: same context, same result, no clock and no
    model. That is what makes it arguable — the approval screen shows the
    factors, and a reviewer who thinks a factor is wrong can say so precisely.
    """
    factors: list[RiskFactor] = []

    def add(code: str, detail: str) -> None:
        factors.append(RiskFactor(code=code, weight=WEIGHTS[code], detail=detail))

    field_name = context.field_name.casefold()

    if context.is_gtin_change or field_name in {"gtin", "upc"}:
        add("gtin_modification", "changing a GS1 identifier reassigns product identity")
    if context.is_sku_change or field_name == "sku":
        add("sku_modification", "changing a SKU breaks every downstream reference")
    if context.is_identity_field and not (context.is_gtin_change or field_name in {"gtin", "upc"}):
        add("identity_mismatch", f"{context.field_name} participates in product identity")
    if context.is_deletion or field_name in DELETION_FIELDS:
        add("product_deletion", "the product is being removed from the catalogue")
    if context.affects_parent_child:
        add("parent_child_modification", "the product tree is reshaped")
    if context.affects_variants:
        add("variant_modification", "one or more variants change")
    if context.affects_category:
        add("category_modification", "category placement changes")
    if context.affects_compliance or context.compliance_violation_count:
        add(
            "compliance_violation",
            f"{context.compliance_violation_count or 'a'} compliance issue(s) affected",
        )
    if context.is_restriction or field_name in RESTRICTION_FIELDS:
        add("restriction_applied", "a restriction or suppression is applied")
    if context.affects_marketplace_listing or context.affected_channel_count:
        add(
            "marketplace_listing",
            f"the change reaches {context.affected_channel_count or 1} live channel(s)",
        )
    if context.affected_channel_count > BULK_CHANNEL_THRESHOLD:
        add(
            "channel_breadth",
            f"{context.affected_channel_count} channels change at once, so rollback "
            "must work everywhere",
        )
    if context.affects_url_or_slug or field_name in {"url", "slug", "handle", "listing_url"}:
        add("url_or_slug_change", "a public URL changes, breaking inbound links")
    if context.affects_seo_field or field_name in SEO_SENSITIVE_FIELDS:
        add("seo_sensitive_field", f"{context.field_name} affects how the product is indexed")
    if field_name in TITLE_FIELDS:
        add("title_change", "the product title changes on live listings")
    if field_name in DESCRIPTION_FIELDS:
        add("description_change", "the product description changes")
    if context.has_sales_history and context.estimated_daily_units >= HIGH_DAILY_UNITS:
        add(
            "sales_impact",
            f"about {context.estimated_daily_units:.0f} units/day move if this is wrong",
        )
    if context.monthly_revenue_at_risk >= HIGH_REVENUE_AT_RISK:
        add(
            "revenue_at_risk",
            f"{context.monthly_revenue_at_risk} in monthly revenue is exposed",
        )
    if context.affected_product_count >= BULK_PRODUCT_THRESHOLD:
        add(
            "bulk_change",
            f"{context.affected_product_count} products are affected in one change",
        )
    if context.confidence < LOW_CONFIDENCE_THRESHOLD:
        add(
            "low_confidence",
            f"the change is only {context.confidence:.0%} confident",
        )
    if context.evidence_count <= THIN_EVIDENCE_THRESHOLD:
        add(
            "thin_evidence",
            f"only {context.evidence_count} evidence item(s) support the change",
        )

    score = sum(f.weight for f in factors)
    level = classify(score)

    escalated: str | None = None
    for code, floor in FLOORS:
        if any(f.code == code for f in factors):
            floor_level = classify(floor_rank(floor))
            if floor_level.rank > level.rank:
                level = floor_level
                escalated = (
                    f"{code} raises the band to {floor.value} regardless of the "
                    f"summed score of {score}"
                )

    factors = sorted(factors, key=lambda f: (-f.weight, f.code))
    return RiskResult(
        level=level,
        numeric_score=score,
        factors=tuple(factors),
        escalated_reason=escalated,
    )


def floor_rank(level: RiskLevel) -> int:
    """The score a floor band corresponds to, for comparison with the total."""
    for threshold, banded in THRESHOLDS:
        if banded is level:
            return threshold
    return 0


def context_from_change(
    *,
    field_name: str,
    current_value: object = None,
    proposed_value: object = None,
    affected_channels: list[str] | None = None,
    affected_product_ids: list[str] | None = None,
    relationship_types: set[str] | None = None,
    confidence: float = 1.0,
    evidence: list[object] | None = None,
    compliance_violation_count: int = 0,
    has_sales_history: bool = False,
    estimated_daily_units: float = 0.0,
    monthly_revenue_at_risk: Decimal = Decimal(0),
) -> RiskContext:
    """Build a `RiskContext` from a change proposal.

    A thin adapter over the ORM so the engine itself stays free of SQLAlchemy.
    Field classification is inferred from the field name here rather than in the
    engine, which keeps "what counts as identity" a single readable table.
    """
    normalized_field = field_name.casefold()
    structural = bool(relationship_types and STRUCTURAL_RELATIONSHIPS & set(relationship_types))
    return RiskContext(
        field_name=normalized_field,
        is_identity_field=normalized_field in IDENTITY_FIELDS,
        is_gtin_change=normalized_field in {"gtin", "upc"},
        is_sku_change=normalized_field == "sku",
        affects_parent_child=structural,
        affects_variants=normalized_field in {"size", "color", "material", "style"}
        or structural,
        affects_category=normalized_field in {"category_path", "category"},
        affects_seo_field=normalized_field in SEO_SENSITIVE_FIELDS,
        affects_compliance=normalized_field in RESTRICTION_FIELDS,
        affects_marketplace_listing=bool(affected_channels),
        affects_url_or_slug=normalized_field in {"url", "slug", "handle", "listing_url"},
        is_deletion=current_value is not None and proposed_value is None,
        is_restriction=normalized_field in RESTRICTION_FIELDS,
        relationship_types=frozenset(relationship_types or ()),
        affected_channel_count=len(affected_channels or ()),
        affected_product_count=max(len(affected_product_ids or ()), 1),
        has_sales_history=has_sales_history,
        estimated_daily_units=estimated_daily_units,
        monthly_revenue_at_risk=monthly_revenue_at_risk,
        compliance_violation_count=compliance_violation_count,
        evidence_count=len(evidence or ()),
        confidence=confidence,
    )
