"""Conflict detection (§11, D-015).

§11 is explicit that the system "must not simply select one" when sources
disagree, and it lists what an answer must contain: available values, source
authority, evidence, confidence, potential impact, and a proposed canonical
value.

Two consequences shape this module:

- **A conflict and a proposal are different objects.** A conflict is the record
  of a disagreement; a proposal is a candidate change awaiting approval. A
  conflict with a clean authority winner still produces a proposal; a conflict
  with no winner produces neither, because there is nothing to propose yet.
- **Potential impact is an input, not a conclusion.** The risk engine scores it;
  this module reports the inputs (which fields, which channels, how many
  children affected) so the number is traceable to something checkable.

Everything here is deterministic. §11 forbids an LLM from picking the value, and
an LLM is not involved.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

from app.models.catalog import SourceType
from app.models.reconciliation import ConflictType
from app.services.catalog.authority import (
    AuthorityDecision,
    AuthorityResolution,
    Candidate,
    conflict_evidence,
    resolve,
)
from app.services.catalog.identity import _color_equivalent, normalize_color


class ConflictSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class FieldObservation:
    """What one source said about one field."""

    source: SourceType
    value: object
    source_label: str | None = None
    record_id: str | None = None
    is_current_canonical: bool = False
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class FieldComparison:
    """Per-field disagreement, before authority is consulted."""

    field_name: str
    observations: tuple[FieldObservation, ...]
    normalized_values: dict[str, object] = field(default_factory=dict)

    @property
    def available_values(self) -> dict[str, object]:
        """What each source literally claims, keyed by source.

        Shown as written rather than grouped: the person deciding needs to see
        "Amazon says Blue", not "color:dark blue".
        """
        return dict(self.normalized_values)

    @property
    def equivalent_groups(self) -> dict[str, list[str]]:
        """Sources grouped by what they mean, so spelling is not substance.

        `Navy Blue`, `Blue` and `Dark Blue` are one argument about colour, not
        three separate disagreements.
        """
        groups: dict[str, list[str]] = {}
        for source, value in self.normalized_values.items():
            groups.setdefault(_normal_form(self.field_name, value), []).append(source)
        return dict(sorted(groups.items()))

    @property
    def distinct_groups(self) -> int:
        return len(self.equivalent_groups)

    @property
    def agrees(self) -> bool:
        return self.distinct_groups <= 1


@dataclass(frozen=True, slots=True)
class DetectedConflict:
    """§11's required content, in one object."""

    product_id: str | None
    field_name: str
    conflict_type: ConflictType
    severity: ConflictSeverity
    available_values: dict[str, object]
    authority: AuthorityResolution
    confidence: float
    proposed_value: object | None
    proposed_source: str | None
    requires_approval: bool
    evidence: list[dict[str, object]] = field(default_factory=list)
    impact: dict[str, object] = field(default_factory=dict)
    explanation: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "product_id": self.product_id,
            "field": self.field_name,
            "type": self.conflict_type.value,
            "severity": self.severity.value,
            "available_values": self.available_values,
            "authority": conflict_evidence(self.authority),
            "confidence": round(self.confidence, 4),
            "proposed_value": self.proposed_value,
            "proposed_source": self.proposed_source,
            "requires_approval": self.requires_approval,
            "evidence": self.evidence,
            "impact": self.impact,
            "explanation": self.explanation,
        }


# Fields where a disagreement is a factual contradiction rather than a matter of
# taste. A price that differs by currency unit is a bug in someone's feed; a
# title that differs is an editorial choice.
IDENTITY_FIELDS: frozenset[str] = frozenset({"gtin", "upc", "mpn", "sku"})
# Fields where only some sources having a value is itself the problem.
COMPLETENESS_FIELDS: frozenset[str] = frozenset(
    {"gtin", "description", "image_url", "brand", "category_path"}
)


def _normal_form(field_name: str, value: object) -> str:
    """Collapse cosmetic differences so one disagreement is reported once.

    Numeric strings join the numbers. An ERP exporting `10.0` and a marketplace
    exporting `"10.00"` agree on the price; treating that as a conflict would
    fill the approval queue with a hundred no-op price changes a day.
    """
    if value is None:
        return "∅"
    if field_name in {"color", "colour"}:
        return f"color:{normalize_color(str(value))}"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float):
        return f"num:{float(value):.6f}"
    text = str(value).strip().casefold()
    try:
        return f"num:{float(text.replace(',', '')):.6f}"
    except ValueError:
        pass
    return text if len(text) > 1 or text.isdigit() else text


def compare_field(field_name: str, observations: list[FieldObservation]) -> FieldComparison:
    """Group observations of one field by what they actually claim."""
    normalized = {obs.source.value: obs.value for obs in observations if obs.value is not None}
    return FieldComparison(
        field_name=field_name,
        observations=tuple(observations),
        normalized_values=normalized,
    )


def _conflict_type(field_name: str) -> ConflictType:
    if field_name in IDENTITY_FIELDS:
        return ConflictType.SKU_CONFLICT if field_name == "sku" else ConflictType.IDENTITY_MISMATCH
    if field_name in {"color", "size", "material", "style"}:
        return ConflictType.VARIANT_CONFLICT
    return ConflictType.ATTRIBUTE_MISMATCH


def _severity(field_name: str, comparison: FieldComparison) -> ConflictSeverity:
    if field_name in IDENTITY_FIELDS:
        return ConflictSeverity.ERROR
    if field_name in {"price", "cost", "gtin", "category_path"}:
        return ConflictSeverity.ERROR
    if comparison.observations and all(
        obs.source in {SourceType.MARKETPLACE, SourceType.HISTORICAL}
        for obs in comparison.observations
    ):
        # Marketplace disagreement with itself changes no canonical record.
        return ConflictSeverity.INFO
    return ConflictSeverity.WARNING


def _confidence(
    resolution: AuthorityResolution,
    severity: ConflictSeverity,
    corroborating_sources: int,
) -> float:
    """How sure we are about the *authority* answer, not about the change.

    A tie has no answer at all, so this is 0 rather than a low probability of
    being right — there is no answer to be confident about.

    Corroboration raises it because independent sources agreeing on the same
    value is real evidence; a field where the only agreeing voices are
    marketplaces is weaker, so that discount is applied too.
    """
    if not resolution.resolved or resolution.winner is None:
        return 0.0
    score = 0.80 + min(corroborating_sources, 3) * 0.05
    if severity is ConflictSeverity.INFO:
        score -= 0.15
    return min(score, 0.98)


def _corroboration(comparison: FieldComparison, winner: Candidate | None) -> int:
    """How many other sources independently agree with the winner."""
    if winner is None:
        return 0
    form = _normal_form(comparison.field_name, winner.value)
    return sum(
        1
        for source, value in comparison.normalized_values.items()
        if source != winner.source.value
        and _normal_form(comparison.field_name, value) == form
    )


def _impact(
    field_name: str,
    comparison: FieldComparison,
    affected_channels: list[str],
    affected_products: list[str],
) -> dict[str, object]:
    """The measurable inputs to risk, recorded so the score can be audited."""
    return {
        "field": field_name,
        "source_count": len({obs.source for obs in comparison.observations}),
        "distinct_value_count": comparison.distinct_groups,
        "affected_channels": affected_channels,
        "affected_products": affected_products,
        "is_current_canonical_change": any(
            obs.is_current_canonical for obs in comparison.observations
        ),
    }


def detect_field_conflicts(
    product_id: str | None,
    observations: dict[str, list[FieldObservation]],
    affected_channels: list[str] | None = None,
    affected_products: list[str] | None = None,
    overrides: dict[str, dict[SourceType, int]] | None = None,
) -> list[DetectedConflict]:
    """Find every field whose sources disagree, and say what to do about each.

    Agreement produces nothing. That is the common case and it should be quiet:
    a reconciler that reports every field it checked would bury the three that
    matter.
    """
    channels = affected_channels or []
    products = affected_products or []
    conflicts: list[DetectedConflict] = []

    for field_name, field_observations in observations.items():
        usable = [obs for obs in field_observations if obs.value is not None]
        if not usable:
            continue
        comparison = compare_field(field_name, usable)
        if comparison.agrees:
            continue
        equivalent = comparison.equivalent_groups

        resolution = resolve(
            field_name,
            [
                Candidate(
                    source=obs.source,
                    value=obs.value,
                    source_label=obs.source_label,
                    source_record_id=obs.record_id,
                    observed_at=obs.updated_at,
                )
                for obs in usable
            ],
            overrides=overrides,
        )
        severity = _severity(field_name, comparison)
        conflict_type = _conflict_type(field_name)
        winner = resolution.winner

        evidence = conflict_evidence(resolution)
        evidence["equivalent_groups"] = equivalent
        evidence["source_records"] = [
            {
                "source": obs.source.value,
                "label": obs.source_label,
                "record_id": obs.record_id,
                "is_current_canonical": obs.is_current_canonical,
                "updated_at": obs.updated_at,
            }
            for obs in usable
        ]
        if field_name in {"color", "colour"}:
            # Suppliers name the same colour differently; saying so explains why
            # this is one argument rather than three.
            groups: dict[str, list[str]] = {}
            for obs in usable:
                groups.setdefault(_normal_form(field_name, obs.value), []).append(
                    str(obs.value)
                )
            evidence["normalized_groups"] = groups

        conflicts.append(
            DetectedConflict(
                product_id=product_id,
                field_name=field_name,
                conflict_type=conflict_type,
                severity=severity,
                available_values=comparison.available_values,
                authority=resolution,
                confidence=_confidence(
                    resolution, severity, _corroboration(comparison, winner)
                ),
                proposed_value=winner.value if winner else None,
                proposed_source=winner.source.value if winner else None,
                # A tie is escalated for a human; a clean winner still needs
                # approval, because §11's premise is that no value is applied
                # without one.
                requires_approval=True,
                evidence=[evidence],
                impact=_impact(field_name, comparison, channels, products),
                explanation=(
                    resolution.reason
                    if resolution.resolved
                    else f"{resolution.reason}; no value is proposed"
                ),
            )
        )
    return conflicts


def detect_missing_attribute_conflicts(
    product_id: str | None,
    required: dict[str, list[FieldObservation]],
) -> list[DetectedConflict]:
    """Fields some sources have and others lack.

    Separate from disagreement: every source that has the field agrees about
    it, and the problem is that a required attribute is missing entirely.
    """
    conflicts: list[DetectedConflict] = []
    for field_name, field_observations in required.items():
        present = [obs for obs in field_observations if obs.value is not None]
        absent = [obs for obs in field_observations if obs.value is None]
        if not present or not absent:
            continue
        winner_source = present[0]
        conflicts.append(
            DetectedConflict(
                product_id=product_id,
                field_name=field_name,
                conflict_type=ConflictType.MISSING_ATTRIBUTE,
                severity=ConflictSeverity.WARNING,
                available_values={obs.source.value: obs.value for obs in present},
                authority=AuthorityResolution(
                    field_name=field_name,
                    decision=AuthorityDecision.RESOLVED,
                    winner=Candidate(source=winner_source.source, value=winner_source.value),
                    candidates=tuple(
                        Candidate(source=obs.source, value=obs.value)
                        for obs in present
                    ),
                    reason=f"{winner_source.source.value} is the only source with {field_name}",
                ),
                confidence=0.9,
                proposed_value=winner_source.value,
                proposed_source=winner_source.source.value,
                requires_approval=False,
                evidence=[
                    {
                        "missing_from": [obs.source.value for obs in absent],
                        "present_in": [obs.source.value for obs in present],
                    }
                ],
                impact={"field": field_name, "sources_missing": len(absent)},
                explanation=(
                    f"{field_name} is absent from "
                    f"{', '.join(sorted(obs.source.value for obs in absent))}; "
                    f"{winner_source.source.value} supplies it"
                ),
            )
        )
    return conflicts


def detect_color_conflicts(
    product_id: str | None,
    observations: list[FieldObservation],
) -> list[DetectedConflict]:
    """A dedicated pass for the §11 example: Navy Blue / Blue / Dark Blue.

    Grouped by color equivalence first, so four sources that all mean navy
    produce one conflict with one winner rather than three pairwise arguments —
    and so a naming difference that means the same colour is not reported as a
    disagreement at all.
    """
    distinct: dict[str, list[FieldObservation]] = {}
    present = [obs for obs in observations if obs.value is not None]
    for obs in present:
        distinct.setdefault(_normal_form("color", obs.value), []).append(obs)
    if len(distinct) <= 1:
        return []
    conflicts = detect_field_conflicts(product_id, {"color": present})
    for conflict in conflicts:
        conflict.evidence.append(
            {
                "sources_by_color_group": {
                    form: sorted({obs.source.value for obs in group})
                    for form, group in sorted(distinct.items())
                },
                "spellings_by_color_group": {
                    form: sorted({str(obs.value) for obs in group})
                    for form, group in sorted(distinct.items())
                },
                "distinct_color_groups": len(distinct),
                "pairwise_equivalent": [
                    {
                        "a": str(a.value),
                        "b": str(b.value),
                        "equivalent": _color_equivalent(str(a.value), str(b.value)),
                    }
                    for a in present
                    for b in present
                    if a is not b
                ],
            }
        )
    return conflicts


def conflict_summary(conflicts: list[DetectedConflict]) -> dict[str, object]:
    """Counts for `ReconciliationRun.conflicts_found` and the health score."""
    by_type = Counter(c.conflict_type.value for c in conflicts)
    by_severity = Counter(c.severity.value for c in conflicts)
    tied = sum(1 for c in conflicts if c.authority.decision is AuthorityDecision.TIED)
    return {
        "total": len(conflicts),
        "errors": by_severity["error"],
        "warnings": by_severity["warning"],
        "info": by_severity["info"],
        "tied": tied,
        "escalated": sum(1 for c in conflicts if c.requires_approval),
        **{f"type.{k}": v for k, v in sorted(by_type.items())},
    }
