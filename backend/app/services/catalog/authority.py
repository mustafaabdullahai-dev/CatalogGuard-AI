"""Source authority resolution (D-015).

Answers one question: *for this field, which source gets to be right?*

The default ladder is the one in D-002 — manufacturer, then ERP/PIM/WMS, then
internal, then marketplace, historical, AI inference — with per-field overrides
on top, because the ladder alone is wrong in a way businesses feel immediately:
a marketplace title is not authoritative over the PIM's, but the marketplace
price is the only price a shopper sees, and "correct" is not the same question as
"live".

Two rules that are not negotiable:

1. **A tie is a conflict, never a coin flip.** ERP and PIM share a rank by
   default. If they disagree and no per-field rule separates them, the field is
   escalated. Picking one silently would make the outcome depend on iteration
   order.
2. **AI inference never wins.** It is ranked last by default and
   `AI_INFERRED_IS_NEVER_AUTHORITATIVE` holds even if a tenant's override map
   says otherwise. An inferred value presented as authoritative is how a
   hallucination becomes the canonical record.

Overrides are per (workspace, field). A workspace that disagrees with the
defaults changes its own catalog, not everyone else's.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.models.catalog import SourceType


class AuthorityDecision(StrEnum):
    """Why the resolver answered the way it did."""

    RESOLVED = "resolved"
    # Rank is equal across the disagreeing sources and no override separates
    # them. Escalate; do not choose.
    TIED = "tied"
    # Sources exist but every candidate value is nullish.
    NO_VALUE = "no_value"
    # Every candidate was AI-inferred, or was otherwise barred from authority.
    NO_AUTHORITATIVE_SOURCE = "no_authoritative_source"


# Per-field default overrides, applied on top of `SourceType.default_rank`.
#
# Rationale per group:
#   - Cost and margin belong to the ERP; a PIM copy is downstream of finance.
#   - Titles and descriptions are editorial, and the PIM is where they are
#     authored; a marketplace rewrites them for its own search behaviour.
#   - Inventory quantity is the WMS's count, not a document's claim.
#   - Identifiers are the manufacturer's. Nobody downstream may invent a GTIN.
#   - Dimensions and weight come from the manufacturer when available because a
#     rescaled "correct" value would be wrong in a way no one notices until a
#     customs form is rejected.
FIELD_RANK_OVERRIDES: dict[str, dict[SourceType, int]] = {
    "price": {
        SourceType.MANUFACTURER: 1,
        SourceType.ERP: 2,
        SourceType.MARKETPLACE: 3,
        SourceType.PIM: 4,
    },
    "cost": {SourceType.MANUFACTURER: 1, SourceType.ERP: 2, SourceType.PIM: 5},
    "currency": {SourceType.MANUFACTURER: 1, SourceType.ERP: 2, SourceType.PIM: 5},
    "title": {
        SourceType.PIM: 1,
        SourceType.MANUFACTURER: 2,
        SourceType.ERP: 3,
        SourceType.MARKETPLACE: 4,
    },
    "description": {
        SourceType.PIM: 1,
        SourceType.MANUFACTURER: 2,
        SourceType.MARKETPLACE: 4,
    },
    "gtin": {SourceType.MANUFACTURER: 1, SourceType.ERP: 2},
    "upc": {SourceType.MANUFACTURER: 1, SourceType.ERP: 2},
    "mpn": {SourceType.MANUFACTURER: 1, SourceType.ERP: 2},
    "sku": {SourceType.INTERNAL: 1, SourceType.MANUFACTURER: 2, SourceType.ERP: 3},
    "inventory": {SourceType.WMS: 1, SourceType.ERP: 2, SourceType.MANUFACTURER: 3},
    "weight": {SourceType.MANUFACTURER: 1, SourceType.ERP: 2},
    "length": {SourceType.MANUFACTURER: 1, SourceType.ERP: 2},
    "width": {SourceType.MANUFACTURER: 1, SourceType.ERP: 2},
    "height": {SourceType.MANUFACTURER: 1, SourceType.ERP: 2},
}

# Inferred values can inform a proposal but never become the canonical value.
BARRED_SOURCES: frozenset[SourceType] = frozenset({SourceType.AI_INFERRED})


@dataclass(frozen=True, slots=True)
class Candidate:
    """One source's claim about one field."""

    source: SourceType
    value: object
    source_label: str | None = None
    source_record_id: str | None = None
    observed_at: str | None = None

    def describe(self) -> str:
        return f"{self.source.value}: {self.value!r}"


@dataclass(frozen=True, slots=True)
class AuthorityResolution:
    """The resolver's answer, including the case where it declines to answer."""

    field_name: str
    decision: AuthorityDecision
    winner: Candidate | None = None
    candidates: tuple[Candidate, ...] = ()
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return self.decision is AuthorityDecision.RESOLVED and self.winner is not None

    @property
    def needs_escalation(self) -> bool:
        return self.decision in {
            AuthorityDecision.TIED,
            AuthorityDecision.NO_AUTHORITATIVE_SOURCE,
        }

    @property
    def competing_values(self) -> dict[str, object]:
        """Source → value, for the conflict record the model already has."""
        return {c.source.value: c.value for c in self.candidates}


def rank_for(
    field_name: str,
    source: SourceType,
    overrides: dict[str, dict[SourceType, int]] | None = None,
) -> int:
    """Effective rank for a source on a field.

    An override entry replaces the default rank outright rather than shifting
    it, because a relative nudge ("ERP is a bit better than PIM here") is not
    expressible as a number and is not a decision anyone can review.
    """
    table = overrides if overrides is not None else FIELD_RANK_OVERRIDES
    if field_name in table and source in table[field_name]:
        return table[field_name][source]
    return source.default_rank


def _comparable(value: object) -> object:
    """Values compare by their normalized form, not their source type.

    `Decimal("10.0")` and `"10.00"` are the same price from two systems; without
    this they would register as a conflict and manufacture work for a human.
    """
    if isinstance(value, str):
        return value.strip().casefold()
    return value


def resolve(
    field_name: str,
    candidates: list[Candidate],
    overrides: dict[str, dict[SourceType, int]] | None = None,
) -> AuthorityResolution:
    """Decide the canonical value for one field, or decline to."""
    usable = [c for c in candidates if c.value is not None and c.value != ""]
    if not usable:
        return AuthorityResolution(
            field_name=field_name,
            decision=AuthorityDecision.NO_VALUE,
            reason="every source supplied no value for this field",
        )

    eligible = [c for c in usable if c.source not in BARRED_SOURCES]
    if not eligible:
        return AuthorityResolution(
            field_name=field_name,
            decision=AuthorityDecision.NO_AUTHORITATIVE_SOURCE,
            candidates=tuple(usable),
            reason=(
                "only AI-inferred values exist; inference may inform a proposal "
                "but is never authoritative"
            ),
        )

    ranked = sorted(
        eligible,
        key=lambda c: (rank_for(field_name, c.source, overrides), c.source.value),
    )
    best = ranked[0]
    best_rank = rank_for(field_name, best.source, overrides)

    # Peers at the same rank that disagree. Agreement among equals is not a
    # conflict, so only differing values escalate.
    disagreeing_peers = [
        c
        for c in ranked
        if rank_for(field_name, c.source, overrides) == best_rank
        and _comparable(c.value) != _comparable(best.value)
    ]
    if disagreeing_peers:
        at_best = {
            c.source
            for c in ranked
            if rank_for(field_name, c.source, overrides) == best_rank
        }
        peers = sorted(at_best, key=lambda s: s.value)
        return AuthorityResolution(
            field_name=field_name,
            decision=AuthorityDecision.TIED,
            candidates=tuple(ranked),
            reason=(
                f"{' and '.join(p.value for p in peers)} share rank {best_rank} for "
                f"{field_name} and disagree; escalating rather than choosing"
            ),
        )

    # A lower-ranked source agreeing with the winner adds evidence rather than
    # a competing claim.
    agreeing = [
        c for c in eligible if _comparable(c.value) == _comparable(best.value)
    ]
    reason = (
        f"{best.source.value} is authoritative for {field_name}"
        f" (rank {best_rank})"
    )
    if len(agreeing) > 1:
        others = sorted(c.source.value for c in agreeing if c is not best)
        reason += f"; corroborated by {', '.join(others)}"
    return AuthorityResolution(
        field_name=field_name,
        decision=AuthorityDecision.RESOLVED,
        winner=best,
        candidates=tuple(ranked),
        reason=reason,
    )


def conflict_evidence(resolution: AuthorityResolution) -> dict[str, object]:
    """Shape the explanation a conflict record needs.

    Section 11 requires available values, authority, evidence, confidence and
    impact. This returns everything except impact, which belongs to the risk
    engine — that separation is why a conflict can be recorded before anyone
    decides how dangerous it is.
    """
    ranks = {
        c.source.value: rank_for(resolution.field_name, c.source)
        for c in resolution.candidates
    }
    return {
        "field": resolution.field_name,
        "values": resolution.competing_values,
        "ranks": ranks,
        "reason": resolution.reason,
        "decision": resolution.decision.value,
        "winner": resolution.winner.value if resolution.winner else None,
        "winner_source": resolution.winner.source.value if resolution.winner else None,
        "barred_sources": sorted(s.value for s in BARRED_SOURCES),
    }
