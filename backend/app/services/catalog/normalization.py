"""Catalog normalization (D-015).

The intake agent's job: turn whatever a supplier sent into one canonical shape.
Most of this is spelling — `colour`, `color`, `product_color` and `variant_color`
are one field, and treating them as four is how a catalog ends up with four
values for the same attribute.

Why the mapping is data, not code
    Section 7 of the requirements says an equivalence mapping "must be
    validated". So aliases live in an explicit table with an optional synonym
    weight, every normalization emits the rule that fired, and nothing is
    normalized into a canonical field that is not on the target list. An
    unmapped column is reported as unknown rather than guessed at: a wrong
    canonical field is worse than an unmapped one, because it corrupts data that
    later looks authoritative.

What is deliberately not here
    No fuzzy field-name matching at runtime. "product_colour" and
    "vendorColorName" mapping to each other is a judgement call that belongs in a
    reviewed mapping table, not in a per-row string comparison that nobody
    audits. `propose_aliases` surfaces those candidates for a human instead.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

# Canonical field names. Normalization can only produce one of these, so a
# target is never invented.
CANONICAL_FIELDS: frozenset[str] = frozenset(
    {
        "sku",
        "mpn",
        "gtin",
        "upc",
        "brand",
        "manufacturer",
        "title",
        "description",
        "category_path",
        "color",
        "size",
        "material",
        "style",
        "price",
        "cost",
        "currency",
        "weight",
        "length",
        "width",
        "height",
        "image_url",
    }
)

# `variant_color` and `product_color` are namespaced aliases of `color`, not a
# separate concept: a variant's colour is the same attribute as the product's.
_ALIASES: dict[str, str] = {
    # identity
    "itemid": "sku",
    "item_id": "sku",
    "productcode": "sku",
    "product_code": "sku",
    "style_code": "sku",
    "stylecode": "sku",
    "vendor_sku": "sku",
    "manufacturermpn": "mpn",
    "manufacturer_mpn": "mpn",
    "model": "mpn",
    "modelnumber": "mpn",
    "model_number": "mpn",
    "gtin13": "gtin",
    "gtin_13": "gtin",
    "gtin14": "gtin",
    "ean": "gtin",
    "ean13": "gtin",
    "upc_code": "upc",
    # organization
    "brandname": "brand",
    "brand_name": "brand",
    "manufacturername": "manufacturer",
    "manufacturer_name": "manufacturer",
    "vendor": "manufacturer",
    "supplier": "manufacturer",
    # content
    "productname": "title",
    "product_name": "title",
    "producttitle": "title",
    "product_title": "title",
    "name": "title",
    "longdescription": "description",
    "productdescription": "description",
    "product_description": "description",
    "category": "category_path",
    "categories": "category_path",
    "categorypath": "category_path",
    "category_path_full": "category_path",
    # variation
    "colour": "color",
    "colours": "color",
    "color_name": "color",
    "product_color": "color",
    "productcolour": "color",
    "variant_color": "color",
    "variantcolour": "color",
    "colour_name": "color",
    "sizes": "size",
    "size_name": "size",
    "variant_size": "size",
    "materialtype": "material",
    "material_type": "material",
    "fabric": "material",
    "style_name": "style",
    "variant_style": "style",
    # commercial
    "listprice": "price",
    "list_price": "price",
    "unitprice": "price",
    "unit_price": "price",
    "retailprice": "price",
    "retail_price": "price",
    "ourprice": "cost",
    "unitcost": "cost",
    "suppliercost": "cost",
    "supplier_cost": "cost",
    "purchaseprice": "cost",
    "iso_currency": "currency",
    # physical
    "productweight": "weight",
    "product_weight": "weight",
    "unitweight": "weight",
    "itemweight": "weight",
    "packagelengt": "length",
    "packagelength": "length",
    "packagelenght": "length",
    "packagelengthh": "length",
    "packagewidth": "width",
    "packageheight": "height",
    "image": "image_url",
    "imageurl": "image_url",
    "primaryimage": "image_url",
    "primary_image": "image_url",
    "img_url": "image_url",
    "mainimage": "image_url",
    # `colour`/`packagelenght` above are deliberate: these misspellings appear in
    # real supplier exports and would otherwise land as unknown fields.
}

# Values that mean "no value" rather than "a value". Treating these as data
# creates phantom conflicts — one source saying "N/A" and another saying "Blue"
# looks like a disagreement about color.
NULLISH = frozenset(
    {"", "-", "--", "n/a", "na", "none", "null", "nil", "unknown", "not applicable", "tbd"}
)

_UNITS: dict[str, tuple[str, Decimal]] = {
    # unit: (canonical unit, multiplier into it)
    "mm": ("mm", Decimal(1)),
    "millimeter": ("mm", Decimal(1)),
    "millimetre": ("mm", Decimal(1)),
    "cm": ("cm", Decimal(1)),
    "centimeter": ("cm", Decimal(1)),
    "centimetre": ("cm", Decimal(1)),
    "m": ("m", Decimal(1)),
    "meter": ("m", Decimal(1)),
    "metre": ("m", Decimal(1)),
    "kg": ("kg", Decimal(1)),
    "kilogram": ("kg", Decimal(1)),
    "kgs": ("kg", Decimal(1)),
    "g": ("g", Decimal(1)),
    "gram": ("g", Decimal(1)),
    "lb": ("lb", Decimal(1)),
    "lbs": ("lb", Decimal(1)),
    "pound": ("lb", Decimal(1)),
    "oz": ("oz", Decimal(1)),
    "ounce": ("oz", Decimal(1)),
    "in": ("in", Decimal(1)),
    "inch": ("in", Decimal(1)),
    '"""': ("in", Decimal(1)),
    "''": ("in", Decimal(1)),
    "ft": ("ft", Decimal(1)),
    "foot": ("ft", Decimal(1)),
    "feet": ("ft", Decimal(1)),
    "yd": ("yd", Decimal(1)),
}

_LENGTH_TO_MM: dict[str, Decimal] = {
    "mm": Decimal(1),
    "cm": Decimal(10),
    "m": Decimal(1000),
    "in": Decimal("25.4"),
    "ft": Decimal("304.8"),
    "yd": Decimal("914.4"),
}

_MASS_TO_KG: dict[str, Decimal] = {
    "g": Decimal("0.001"),
    "kg": Decimal(1),
    "oz": Decimal("0.028349523125"),
    "lb": Decimal("0.45359237"),
}

_CURRENCY_ALIASES: dict[str, str] = {
    "$": "USD",
    "usd": "USD",
    "us$": "USD",
    "us dollars": "USD",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "¥": "JPY",
    "jpy": "JPY",
    "cad": "CAD",
    "aud": "AUD",
    "inr": "INR",
    "aed": "AED",
    "sar": "SAR",
}

# Every spelling a column name might use, resolved to its canonical field. Used
# by `propose_aliases`, where the name is not in `_ALIASES` yet.
_SPELLING_TO_CANONICAL: dict[str, str] = {
    **{field: field for field in CANONICAL_FIELDS},
    **_ALIASES,
    **{canonical: canonical for canonical in set(_ALIASES.values())},
}

_SEPARATOR_RE = re.compile(r"\s*(?:>|/|\||::|->|,)\s*")
_WHITESPACE_RE = re.compile(r"\s+")
_DIGIT_RE = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass(frozen=True, slots=True)
class NormalizationRule:
    """The rule that fired, recorded so a human can audit the mapping."""

    field: str
    rule: str
    detail: str


@dataclass(frozen=True, slots=True)
class NormalizedRecord:
    """A canonical record plus everything that could not be normalized."""

    fields: dict[str, object]
    rules: tuple[NormalizationRule, ...] = ()
    unknown_fields: tuple[str, ...] = ()
    dropped_fields: tuple[str, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.unknown_fields and not self.dropped_fields


@dataclass(frozen=True, slots=True)
class FieldResolution:
    """Outcome for one input column."""

    canonical: str | None
    value: object | None
    rules: tuple[NormalizationRule, ...] = field(default_factory=tuple)

    @property
    def resolved(self) -> bool:
        return self.canonical is not None and self.value is not None


def normalize_key(raw: str) -> str:
    """Fold a column name to its comparison form.

    Case, surrounding whitespace, and punctuation are noise: `Product Color`,
    `product_color` and `product-color` are one column name. Digits and unit
    suffixes are kept (`color2` is a second color, not a typo for `color`).
    """
    text = unicodedata.normalize("NFKD", raw)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^0-9a-zA-Z]+", "_", text.strip().lower())
    return text.strip("_")


def canonical_field(raw: str) -> str | None:
    """Map a source column name onto a canonical field, or None if unmapped.

    Three steps, in order of decreasing certainty:
      1. already canonical
      2. explicit alias table
      3. namespaced alias (`product_`/`variant_` prefix stripped)
    """
    key = normalize_key(raw)
    if not key:
        return None
    if key in CANONICAL_FIELDS:
        return key
    if key in _ALIASES:
        return _ALIASES[key]
    for prefix in ("product_", "variant_", "vendor_", "item_"):
        if key.startswith(prefix):
            stripped = key[len(prefix) :]
            if stripped in CANONICAL_FIELDS:
                return stripped
            if stripped in _ALIASES:
                return _ALIASES[stripped]
    return None


def normalize_text(value: object) -> str | None:
    """Trim, collapse whitespace, and reject nullish strings.

    Returns None for a value that carries no information, so downstream stages
    see "missing" rather than "empty".
    """
    if value is None:
        return None
    text = str(value).replace("\u00a0", " ").strip()
    if not text or text.lower() in NULLISH:
        return None
    return _WHITESPACE_RE.sub(" ", text)


def normalize_category_path(value: object) -> str | None:
    """Flatten a category path to `A / B / C` form.

    Accepts the shapes suppliers actually send: a list, a delimited string, or a
    path with a trailing slash. The output format is fixed so two sources
    describing the same taxonomy produce the same string — otherwise
    `Home>Garden` and `Home / Garden` register as a category conflict.
    """
    if value is None:
        return None
    if isinstance(value, list | tuple):
        parts = [normalize_text(part) for part in value]
    else:
        text = normalize_text(value)
        parts = _SEPARATOR_RE.split(text) if text else []
    # A trailing separator leaves an empty final segment; `"Home / "` is one
    # category, not two with a blank.
    cleaned = [str(part).strip() for part in parts if part is not None and str(part).strip()]
    if not cleaned:
        return None
    return " / ".join(cleaned)


def normalize_decimal(value: object) -> Decimal | None:
    """Parse a number written for humans.

    Handles currency symbols, thousands separators, and a leading unit
    (`"1,299.00 USD"`, `" 12.5 cm "`). Returns None rather than guessing when
    there is no number in the string at all.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | Decimal):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    match = _DIGIT_RE.search(str(value).replace(",", ""))
    if not match:
        return None
    try:
        return Decimal(match.group())
    except InvalidOperation:
        return None


def normalize_currency(value: object) -> str | None:
    text = normalize_text(value)
    if text is None:
        return None
    return _CURRENCY_ALIASES.get(text.lower())


def normalize_measure(value: object, dimension: str) -> tuple[Decimal | None, str | None]:
    """Split `"12.5 cm"` into `(Decimal("12.5"), "cm")`.

    Bare numbers keep the supplied unit, so a column already split into value
    and unit columns normalizes without a default unit being invented.
    """
    if dimension not in {"length", "weight", "height", "width"}:
        raise ValueError(f"not a measurable dimension: {dimension}")
    if value is None or value == "":
        return None, None
    if isinstance(value, int | float | Decimal):
        return normalize_decimal(value), None
    text = normalize_text(value)
    if text is None:
        return None, None
    parts = text.split()
    unit = _canonical_unit(parts[-1]) if len(parts) > 1 else None
    return normalize_decimal(" ".join(parts[:-1]) if unit else text), unit


def _canonical_unit(token: str) -> str | None:
    return _UNITS.get(token.lower().strip(".")) and _UNITS[token.lower().strip(".")][0]


def convert_measure(
    value: Decimal, unit: str, dimension: str, target: str
) -> Decimal | None:
    """Convert between units of one dimension.

    Deliberately refuses a cross-dimension conversion (`length` → `weight`): a
    30 cm garment is not 30 g of anything, and returning a number would hide the
    supplier's error behind a plausible figure.
    """
    table = _LENGTH_TO_MM if dimension == "length" else _MASS_TO_KG
    if unit not in table or target not in table:
        return None
    converted = value * table[unit] / table[target]
    return converted.quantize(Decimal("0.01"))


_VALUE_NORMALIZERS = {
    "category_path": lambda value: (normalize_category_path(value), "category.path"),
    "color": lambda value: (normalize_text(value), "text.trim"),
    "size": lambda value: (normalize_text(value), "text.trim"),
    "material": lambda value: (normalize_text(value), "text.trim"),
    "style": lambda value: (normalize_text(value), "text.trim"),
    "title": lambda value: (normalize_text(value), "text.trim"),
    "description": lambda value: (normalize_text(value), "text.trim"),
    "brand": lambda value: (normalize_text(value), "text.trim"),
    "manufacturer": lambda value: (normalize_text(value), "text.trim"),
    "sku": lambda value: (normalize_text(value), "text.trim"),
    "mpn": lambda value: (normalize_text(value), "text.trim"),
    "gtin": lambda value: (normalize_identifier(value), "gtin.pad"),
    "upc": lambda value: (normalize_identifier(value), "gtin.pad"),
    "price": lambda value: (normalize_decimal(value), "decimal.parse"),
    "cost": lambda value: (normalize_decimal(value), "decimal.parse"),
    "currency": lambda value: (normalize_currency(value), "currency.code"),
    "length": lambda value: (normalize_measure(value, "length")[0], "measure.length"),
    "width": lambda value: (normalize_measure(value, "width")[0], "measure.width"),
    "height": lambda value: (normalize_measure(value, "height")[0], "measure.height"),
    "weight": lambda value: (normalize_measure(value, "weight")[0], "measure.weight"),
    "image_url": lambda value: (normalize_url(value), "url.strip"),
}


def normalize_identifier(value: object) -> str | None:
    """Normalize a GTIN/UPC to digits.

    GS1 identifiers are digits and nothing else. `EAN-13: 4006381333931` and
    `4006381333931` are the same product code, so stripping non-digits is a
    normalization, not a loss. A value with fewer than 8 digits is rejected:
    that is a quantity or a line number, not a GTIN.
    """
    text = normalize_text(value)
    if text is None:
        return None
    # Stripping every non-digit from `"EAN-13: 4006381333931"` yields
    # `134006381333931`: the label's own digits become part of the code. Take
    # the last long digit run instead, which is the code in every layout seen.
    runs = re.findall(r"\d+", text)
    digits = max((run for run in runs if len(run) >= 8), key=len, default="")
    if len(digits) < 8:
        return None
    return digits.zfill(14) if len(digits) in {12, 13} else digits


def normalize_url(value: object) -> str | None:
    text = normalize_text(value)
    if text is None:
        return None
    if text.startswith("//"):
        text = f"https:{text}"
    elif not re.match(r"https?://", text, re.IGNORECASE):
        text = f"https://{text}"
    return text


def normalize_field(raw_name: str, value: object) -> FieldResolution:
    """Normalize one column, reporting which rule produced the value."""
    target = canonical_field(raw_name)
    if target is None:
        return FieldResolution(canonical=None, value=None)
    normalizer = _VALUE_NORMALIZERS.get(target)
    if normalizer is None:
        text = normalize_text(value)
        return FieldResolution(
            canonical=target,
            value=text,
            rules=(NormalizationRule(target, "text.trim", raw_name),) if text else (),
        )
    normalized, rule_name = normalizer(value)
    if normalized is None:
        return FieldResolution(canonical=target, value=None)
    if isinstance(normalized, tuple):
        normalized, rule_name = normalized
    return FieldResolution(
        canonical=target,
        value=normalized,
        rules=(NormalizationRule(target, rule_name, raw_name),),
    )


def normalize_record(raw: dict[str, object]) -> NormalizedRecord:
    """Normalize a whole intake record.

    Last writer wins for a duplicated canonical field, and the collision is
    recorded as a rule. Silent last-wins would be the alternative, and it would
    mean a supplier's `product_color` quietly overwrote `variant_color` with no
    trace that both existed.
    """
    fields: dict[str, object] = {}
    rules: list[NormalizationRule] = []
    unknown: list[str] = []
    dropped: list[str] = []

    for raw_name, raw_value in raw.items():
        resolution = normalize_field(raw_name, raw_value)
        if resolution.canonical is None:
            # A nullish value for an unknown column is not a problem; a real
            # value under a name we cannot interpret is.
            if normalize_text(raw_value) is not None:
                unknown.append(raw_name)
            else:
                dropped.append(raw_name)
            continue
        if resolution.value is None:
            dropped.append(raw_name)
            continue
        if resolution.canonical in fields:
            rules.append(
                NormalizationRule(
                    resolution.canonical,
                    "duplicate.collision",
                    f"{raw_name} overwrote an earlier column",
                )
            )
        fields[resolution.canonical] = resolution.value
        rules.extend(resolution.rules)

    return NormalizedRecord(
        fields=fields,
        rules=tuple(rules),
        unknown_fields=tuple(unknown),
        dropped_fields=tuple(dropped),
    )


def propose_aliases(records: list[dict[str, object]]) -> dict[str, str]:
    """Suggest aliases for unmapped column names found across records.

    This is the reviewable half of "the mapping must be validated": candidates
    are computed offline from real intake data and a human accepts or rejects
    them. Nothing here is applied automatically.
    """
    counts: dict[str, int] = {}
    for record in records:
        for name in record:
            if canonical_field(name) is None and normalize_text(record[name]) is not None:
                key = normalize_key(name)
                counts[key] = counts.get(key, 0) + 1
    proposals: dict[str, str] = {}
    # Most frequent first, so a reviewer sees the columns that actually recur
    # rather than a one-off typo at the top of the list.
    for key, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        target = _suggest_target(key)
        if target is not None:
            proposals[key] = target
    return proposals


def _suggest_target(key: str) -> str | None:
    """Best canonical guess for an unmapped name, or None.

    Only a unique containment match counts. `colourway` containing `colour` is
    the sort of thing that turns into a wrong mapping if substring proximity is
    allowed to decide, so the candidate must be unambiguous.
    """
    # Both spellings count as evidence: `colourway_name` contains the alias
    # `colour`, not the canonical `color`, and the alias is where the intent is
    # actually written.
    matches = {
        canonical
        for spelling, canonical in _SPELLING_TO_CANONICAL.items()
        if spelling in key
    }
    if len(matches) == 1:
        return matches.pop()
    return None
