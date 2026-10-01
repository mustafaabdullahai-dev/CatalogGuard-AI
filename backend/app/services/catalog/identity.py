"""Product identity resolution (D-015, §8).

Determines whether two records are the same product.

The decision that shapes this module: **only an exact identifier match may link
records automatically.** Everything else — brand plus title, a fuzzy title, an
LLM's opinion — becomes a proposal for a human.

That asymmetry is the point. A missed merge is a duplicate that a human sees.
A wrong merge is unrecoverable: the two products' histories are interleaved, the
canonical record now describes something that never existed, and every prior
approval was granted for the wrong thing. So the auto-link threshold is
"identity was stated, not inferred", and nothing else.

Deterministic matching, then similarity scoring, then (later) LLM reasoning — in
that order, per §8. The LLM is never the decider; it is consulted only for
candidates the deterministic pass already found and scored in the ambiguous
band.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from app.models.catalog import SourceType

# Identifiers strong enough to assert identity on their own. `sku` is excluded
# on purpose: SKUs are vendor-local, so the same string from two vendors is
# routinely two different products, and the same product routinely has three.
IDENTITY_IDENTIFIERS: frozenset[str] = frozenset({"gtin", "upc", "ean", "asin", "isbn"})
# Identifiers that support a match but cannot establish one alone.
SUPPORTING_IDENTIFIERS: frozenset[str] = frozenset({"mpn", "sku"})

# Below this, two records are not the same product regardless of how similar
# their text is. 0.55 keeps unrelated titles apart while still catching the
# truncated-title case ("Navy Cotton T-Shirt" vs "Navy Cotton T Shirt Large").
AUTO_LINK_FLOOR = 0.97
PROPOSE_FLOOR = 0.55
# Above this a candidate is strong enough to auto-link on text plus a supporting
# identifier, if and only if the identifiers agree rather than merely coexist.
STRONG_TEXT_FLOOR = 0.90

# Colour words that describe the same colour. Marketplace titles disagree with
# manufacturer data on colour more than any other attribute ("Navy Blue" vs
# "Blue" vs "Dark Blue"), so treating these as equivalent is what stops the
# reconciler reporting a conflict on every apparel listing.
_COLOR_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"navy", "navy blue", "dark blue"}),
    frozenset({"black", "jet black"}),
    frozenset({"white", "off white", "ivory", "cream"}),
    frozenset({"grey", "gray", "charcoal", "heather grey"}),
    frozenset({"red", "crimson", "scarlet"}),
    frozenset({"blue", "royal blue", "cobalt"}),
    frozenset({"green", "olive", "forest green"}),
    frozenset({"brown", "tan", "beige", "khaki"}),
    frozenset({"pink", "rose", "blush"}),
    frozenset({"purple", "violet", "lavender"}),
    frozenset({"yellow", "mustard"}),
    frozenset({"orange", "burnt orange"}),
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SIZE_RE = re.compile(r"\b(xxs|xs|s|m|l|xl|xxl|xxxl|small|medium|large)\b")
_UNIT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mm|cm|m|in|ft|kg|g|lb|oz)\b")

_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "with", "for", "of", "in", "on", "by",
        "new", "set", "pack", "pcs", "pc", "each", "size", "colour", "color",
        "brand", "free", "shipping", "sale", "best", "quality", "premium",
        "official", "genuine", "hot",
    }
)


# Variation axes that make a record a *different variant* rather than the same
# record described differently. Colour is excluded: suppliers name colours
# inconsistently, so a colour disagreement lowers confidence instead. Size,
# material and style do not have that property — "M" and "L" are two products.
VARIANT_DISQUALIFYING_AXES: frozenset[str] = frozenset({"size", "material", "style"})


class MatchKind(StrEnum):
    """How the match was reached, and therefore what may be done with it."""

    # An identifier stated by both records. May be applied without approval.
    EXACT_IDENTIFIER = "exact_identifier"
    # Deterministic text plus agreeing supporting identifiers. Still proposed.
    STRONG_TEXT = "strong_text"
    # Similar titles, no supporting agreement. Proposed.
    FUZZY = "fuzzy"
    # Same brand, different product. Explicitly not a match.
    DIFFERENT = "different"


@dataclass(frozen=True, slots=True)
class IdentityCandidate:
    """A record being considered for linkage to an existing product."""

    product_id: str | None
    sku: str | None = None
    gtin: str | None = None
    upc: str | None = None
    mpn: str | None = None
    brand: str | None = None
    manufacturer: str | None = None
    title: str | None = None
    description: str | None = None
    attributes: dict[str, object] | None = None
    source: SourceType = SourceType.MARKETPLACE


@dataclass(frozen=True, slots=True)
class Evidence:
    """One reason the score moved, kept for the audit trail."""

    kind: str
    detail: str
    weight: float

    def describe(self) -> str:
        return f"{self.kind}: {self.detail} ({self.weight:+.2f})"


@dataclass(frozen=True, slots=True)
class IdentityMatch:
    """The result, in the shape §8 asks for."""

    match: bool
    confidence: float
    kind: MatchKind
    reason: str
    evidence: tuple[Evidence, ...]
    requires_approval: bool
    proposed_product_id: str | None = None

    def to_payload(self) -> dict[str, object]:
        """Serialize as the requirements' JSON contract."""
        return {
            "match": self.match,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "evidence": [e.describe() for e in self.evidence],
            "kind": self.kind.value,
            "requires_approval": self.requires_approval,
            "product_id": self.proposed_product_id,
        }


def _tokens(text: str | None) -> frozenset[str]:
    if not text:
        return frozenset()
    return frozenset(t for t in _TOKEN_RE.findall(text.casefold()) if t not in _STOPWORDS)


def normalize_color(value: str | None) -> str | None:
    """Collapse a color to its group, so 'Navy Blue' and 'Blue' can compare."""
    if not value:
        return None
    key = re.sub(r"\s+", " ", value.strip().casefold())
    for group in _COLOR_GROUPS:
        if key in group:
            return sorted(group)[0]
    return key


def _color_equivalent(a: str | None, b: str | None) -> bool:
    left, right = normalize_color(a), normalize_color(b)
    return left is not None and left == right


def _gs1(value: str | None) -> str | None:
    """Normalize a GS1 identifier to comparable digits.

    The same code is written `4006381333931` in an EAN-13 file and
    `04006381333931` in a zero-padded GTIN-14 export. Comparing the raw strings
    reports a conflict between a manufacturer and a marketplace over a code
    they agree on, which is precisely the false positive this module exists to
    avoid.
    """
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if not digits:
        return None
    return digits.zfill(14)


def _identifier_conflict(
    left: IdentityCandidate, right: IdentityCandidate
) -> tuple[str, str] | None:
    """Two records stating *different* strong identifiers are not the same thing.

    This is the highest-value check in the module and the reason GTIN beats
    fuzzy text: no amount of title similarity survives one record saying the
    product is GTIN 4006381333931 and the other saying it is 00000000000019.
    """
    pairs = (("gtin", left.gtin, right.gtin), ("upc", left.upc, right.upc))
    for name, a, b in pairs:
        if _gs1(a) and _gs1(b) and _gs1(a) != _gs1(b):
            return name, f"{name} differs: {a} vs {b}"
    return None


def _shared_identifier(left: IdentityCandidate, right: IdentityCandidate) -> str | None:
    for name in IDENTITY_IDENTIFIERS:
        a, b = getattr(left, name, None), getattr(right, name, None)
        if not (a and b):
            continue
        # GTIN and UPC share one GS1 space, so a supplier shipping an EAN and a
        # marketplace the same code as a UPC must not read as a disagreement.
        if _gs1(a) == _gs1(b):
            # One GS1 space: naming the specific field read adds nothing when
            # the caller has to understand the aliases anyway.
            return "gtin/upc/ean" if name in {"gtin", "upc", "ean"} else name
        if a.strip() == b.strip():
            return name
    return None


def _supporting_agreement(left: IdentityCandidate, right: IdentityCandidate) -> list[str]:
    agreed: list[str] = []
    for name in sorted(SUPPORTING_IDENTIFIERS):
        a = getattr(left, name, None)
        b = getattr(right, name, None)
        if a and b and a.strip().casefold() == b.strip().casefold():
            agreed.append(name)
    return agreed


def _brand_conflict(left: IdentityCandidate, right: IdentityCandidate) -> str | None:
    a, b = normalize_color(left.brand), normalize_color(right.brand)
    if a and b and a != b:
        return f"brand differs: {left.brand} vs {right.brand}"
    a = normalize_color(left.manufacturer)
    b = normalize_color(right.manufacturer)
    if a and b and a != b:
        return f"manufacturer differs: {left.manufacturer} vs {right.manufacturer}"
    return None


def similarity(left: IdentityCandidate, right: IdentityCandidate) -> tuple[float, list[Evidence]]:
    """Deterministic 0..1 similarity with the reasons that produced it.

    Built from token overlap and character-level ratio rather than an embedding:
    this runs on every candidate pair during intake, needs to be explainable to
    the person deciding the merge, and must produce the same answer tomorrow.
    """
    evidence: list[Evidence] = []
    left_title = left.title or ""
    right_title = right.title or ""
    left_tokens, right_tokens = _tokens(left_title), _tokens(right_title)
    union = left_tokens | right_tokens
    if not union:
        return 0.0, [Evidence("title", "no comparable title text", 0.0)]

    overlap = left_tokens & right_tokens
    jaccard = len(overlap) / len(union)
    ratio = SequenceMatcher(None, left_title.casefold(), right_title.casefold()).ratio()
    score = 0.6 * jaccard + 0.4 * ratio
    evidence.append(
        Evidence(
            "title_tokens",
            f"{len(overlap)}/{len(union)} significant tokens shared",
            round(0.6 * jaccard, 4),
        )
    )
    evidence.append(
        Evidence("title_chars", f"character similarity {ratio:.2f}", round(0.4 * ratio, 4))
    )

    left_color = _attr(left, "color")
    right_color = _attr(right, "color")
    if left_color and right_color:
        if _color_equivalent(str(left_color), str(right_color)):
            score += 0.05
            evidence.append(Evidence("color", f"both are {normalize_color(str(left_color))}", 0.05))
        else:
            score -= 0.25
            evidence.append(
                Evidence("color", f"{left_color} vs {right_color}", -0.25)
            )

    left_size = _attr(left, "size")
    right_size = _attr(right, "size")
    if left_size and right_size and str(left_size).casefold() != str(right_size).casefold():
        score -= 0.20
        evidence.append(Evidence("size", f"{left_size} vs {right_size}", -0.20))

    if left.mpn and right.mpn and left.mpn.strip().casefold() == right.mpn.strip().casefold():
        score += 0.08
        evidence.append(Evidence("mpn", f"both {left.mpn}", 0.08))

    if _strip_units(left_title) == _strip_units(right_title):
        score += 0.04
        evidence.append(Evidence("title_units", "titles identical ignoring measurements", 0.04))

    return max(0.0, min(1.0, score)), evidence


def _strip_units(title: str) -> str:
    return _UNIT_RE.sub(" ", _SIZE_RE.sub(" ", title)).strip()


def _attr(candidate: IdentityCandidate, key: str) -> object | None:
    return (candidate.attributes or {}).get(key)


def _variant_axis_conflict(
    left: IdentityCandidate, right: IdentityCandidate
) -> tuple[str, str, str] | None:
    """Two records differing on a variant axis are different variants.

    Disqualifying, like a stated identifier conflict: a catalog that merged
    size M into size L would sell one variant out of stock and list the other as
    a duplicate. The text similarity is not consulted, because a variant's title
    is usually identical to its parent's.
    """
    for axis in sorted(VARIANT_DISQUALIFYING_AXES):
        a, b = _attr(left, axis), _attr(right, axis)
        if a in (None, "") or b in (None, ""):
            continue
        if str(a).strip().casefold() != str(b).strip().casefold():
            return axis, str(a), str(b)
    return None


def resolve_identity(
    incoming: IdentityCandidate,
    existing: list[IdentityCandidate],
) -> IdentityMatch:
    """Find the best match for `incoming`, or say plainly that there is none.

    A new product is the common and unremarkable answer: most intake rows are
    new. Returning a low-confidence non-match is correct; manufacturing a weak
    match would put every new listing into the approval queue.
    """
    for candidate in existing:
        if candidate.product_id is None:
            continue
        if incoming.gtin or candidate.gtin or incoming.upc or candidate.upc:
            conflict = _identifier_conflict(incoming, candidate)
            if conflict:
                # A stated identifier disagreement is disqualifying, so the row
                # is not scored at all.
                return IdentityMatch(
                    match=False,
                    confidence=1.0,
                    kind=MatchKind.DIFFERENT,
                    reason=f"not the same product: {conflict[1]}",
                    evidence=(
                        Evidence("identifier_conflict", conflict[1], 0.0),
                    ),
                    requires_approval=False,
                )

    # Second pass: strongest available evidence, and stop at a stated identity.
    best: IdentityMatch | None = None
    for candidate in existing:
        if candidate.product_id is None:
            continue
        shared = _shared_identifier(incoming, candidate)
        if shared:
            return IdentityMatch(
                match=True,
                confidence=1.0,
                kind=MatchKind.EXACT_IDENTIFIER,
                reason=f"identical {shared}; identity stated, not inferred",
                evidence=(Evidence("identifier", f"identical {shared}", 1.0),),
                requires_approval=False,
                proposed_product_id=candidate.product_id,
            )

        axis_conflict = _variant_axis_conflict(incoming, candidate)
        if axis_conflict:
            axis, a, b = axis_conflict
            return IdentityMatch(
                match=False,
                confidence=1.0,
                kind=MatchKind.DIFFERENT,
                reason=f"different variants: {axis} differs, {a} vs {b}",
                evidence=(Evidence("variant_axis", f"{axis}: {a} vs {b}", 0.0),),
                requires_approval=False,
            )

        brand_conflict = _brand_conflict(incoming, candidate)
        score, evidence = similarity(incoming, candidate)
        if brand_conflict:
            score = min(score, PROPOSE_FLOOR - 0.01)
            evidence = [*evidence, Evidence("brand_conflict", brand_conflict, -score)]
        agreeing = _supporting_agreement(incoming, candidate)
        if agreeing:
            score = min(1.0, score + 0.10)
            evidence = [*evidence, Evidence("identifier_agree", ", ".join(agreeing), 0.10)]
        if best is None or score > best.confidence:
            best = IdentityMatch(
                match=score >= PROPOSE_FLOOR,
                confidence=score,
                kind=(
                    MatchKind.STRONG_TEXT
                    if score >= STRONG_TEXT_FLOOR and agreeing
                    else MatchKind.FUZZY
                ),
                reason=(
                    "similar title with agreeing identifiers"
                    if agreeing and score >= STRONG_TEXT_FLOOR
                    else "similar title, no corroborating identifier"
                ),
                evidence=tuple(evidence),
                requires_approval=True,
                proposed_product_id=candidate.product_id,
            )
    if best is None:
        return IdentityMatch(
            match=False,
            confidence=0.0,
            kind=MatchKind.DIFFERENT,
            reason="no existing product to compare against",
            evidence=(Evidence("empty_catalogue", "workspace has no products yet", 0.0),),
            requires_approval=False,
        )
    return best


def needs_review(match: IdentityMatch) -> bool:
    """Whether a human must look at this result.

    Identical identifiers are the only case that clears itself, and even then
    only for linking — merging two products that both exist is always a
    separate, approval-gated action.
    """
    if match.kind is MatchKind.DIFFERENT:
        return False
    return match.requires_approval


def blocking_keys(candidate: IdentityCandidate) -> list[str]:
    """Cheap keys for narrowing a large catalogue before scoring pairs.

    Comparing a new row against every product is quadratic. Identifier keys are
    exact lookups; the title key is a blocking prefix, which is why
    `PROPOSE_FLOOR` cannot be reached by two records sharing only a first word.
    """
    keys: list[str] = []
    for name in sorted(IDENTITY_IDENTIFIERS | SUPPORTING_IDENTIFIERS):
        value = getattr(candidate, name, None)
        if value:
            keys.append(f"{name}:{str(value).strip().casefold()}")
    tokens = sorted(_tokens(candidate.title))
    if tokens:
        keys.append(f"title:{tokens[0]}")
    brand = normalize_color(candidate.brand)
    if brand:
        keys.append(f"brand:{brand}")
    return keys
