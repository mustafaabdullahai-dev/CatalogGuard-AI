"""Schema detection (§49).

Turns parsed columns into a description a human can approve: each column's
canonical target, its inferred type, how often it is null, whether it is unique,
and a few sample values. This is the "schema detection" step, and it is what lets
a reviewer see that `vendorColorName` is unmapped *before* it becomes a silently
ignored column.

Two rules carried over from `services.catalog`:

  - Canonical targets come from `normalization.canonical_field`. A column that
    does not map to a canonical field is reported as unknown, never guessed.
  - Suggested aliases come from `normalization.propose_aliases`, which proposes
    a target only when the containment match is unambiguous. Nothing is applied
    automatically.

Inference is advisory. It exists to catch a price column arriving as text or a
GTIN arriving as a float, and it is deliberately conservative: a value that could
be read two ways is reported as the wider type.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.services.catalog.identity import IDENTITY_IDENTIFIERS, SUPPORTING_IDENTIFIERS
from app.services.catalog.normalization import canonical_field, propose_aliases

_INT_RE = re.compile(r"^-?\d+$")
_DECIMAL_RE = re.compile(r"^-?\d+\.\d+$")
# A leading zero means a code, not a quantity: GTIN-13 arrives as 13 digits and
# would be destroyed by an int cast. So `007` is a string and `7` is an integer.
_ZERO_LEADING_RE = re.compile(r"^-?0\d+$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?")
_BOOL_WORDS = frozenset({"true", "false", "yes", "no"})

# Identity fields an intake record can be matched on (§8). Imported from the
# identity resolver so the two cannot disagree about what counts as identity.
IDENTITY_FIELDS: frozenset[str] = IDENTITY_IDENTIFIERS | SUPPORTING_IDENTIFIERS


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    """What one column looks like, in terms a reviewer can act on."""

    name: str
    canonical: str | None
    inferred_type: str
    null_rate: float
    distinct_count: int
    is_unique: bool
    sample_values: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "canonical": self.canonical,
            "inferred_type": self.inferred_type,
            "null_rate": round(self.null_rate, 4),
            "distinct_count": self.distinct_count,
            "is_unique": self.is_unique,
            "sample_values": list(self.sample_values),
        }


@dataclass(frozen=True, slots=True)
class SchemaDetection:
    """The detected schema for one file, plus what a human should look at."""

    row_count: int
    columns: tuple[ColumnProfile, ...]
    canonical_fields: tuple[str, ...]
    unknown_columns: tuple[str, ...]
    identity_columns: tuple[str, ...]
    suggested_aliases: dict[str, str]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "row_count": self.row_count,
            "columns": [column.as_dict() for column in self.columns],
            "canonical_fields": list(self.canonical_fields),
            "unknown_columns": list(self.unknown_columns),
            "identity_columns": list(self.identity_columns),
            "suggested_aliases": dict(self.suggested_aliases),
            "warnings": list(self.warnings),
        }


def _classify(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, (float, Decimal)):
        return "decimal"
    if isinstance(value, str):
        if _URL_RE.match(value):
            return "url"
        if _DATE_RE.match(value):
            return "date"
        if value.casefold() in _BOOL_WORDS:
            return "boolean"
        if _ZERO_LEADING_RE.match(value):
            return "string"
        if _INT_RE.match(value):
            return "integer"
        if _DECIMAL_RE.match(value):
            return "decimal"
        return "string"
    return "string"


def _infer_type(values: Sequence[object]) -> str:
    kinds = {_classify(value) for value in values if value is not None and value != ""}
    if not kinds:
        return "empty"
    if len(kinds) == 1:
        return kinds.pop()
    return "mixed"


def profile_column(name: str, values: Sequence[object]) -> ColumnProfile:
    total = len(values)
    present = [value for value in values if value is not None and value != ""]
    distinct = len({str(value) for value in present})

    samples: list[str] = []
    for value in present:
        text = str(value)
        if text not in samples:
            samples.append(text)
        if len(samples) >= 3:
            break

    return ColumnProfile(
        name=name,
        canonical=canonical_field(name),
        inferred_type=_infer_type(values),
        null_rate=(total - len(present)) / total if total else 0.0,
        distinct_count=distinct,
        is_unique=bool(present) and distinct == len(present),
        sample_values=tuple(samples),
    )


def detect_schema(
    rows: Sequence[dict[str, object]], columns: Sequence[str]
) -> SchemaDetection:
    """Profile every column and summarise what needs a human's attention."""
    profiles = tuple(
        profile_column(name, [row.get(name) for row in rows]) for name in columns
    )

    canonical = tuple(sorted({p.canonical for p in profiles if p.canonical}))
    unknown = tuple(p.name for p in profiles if p.canonical is None)
    identity = tuple(p.canonical for p in profiles if p.canonical in IDENTITY_FIELDS)

    warnings: list[str] = []
    if not identity:
        warnings.append(
            "no identity column (gtin, upc, ean, mpn or sku) was detected; records "
            "can be captured but cannot be matched to products yet"
        )
    empty = tuple(p.name for p in profiles if p.inferred_type == "empty")
    if empty:
        warnings.append(f"these columns are entirely empty: {', '.join(empty)}")
    if unknown:
        warnings.append(
            f"{len(unknown)} column(s) did not map to a canonical field and will be "
            "stored but not normalized"
        )

    suggested = propose_aliases([dict(row) for row in rows]) if unknown else {}

    return SchemaDetection(
        row_count=len(rows),
        columns=profiles,
        canonical_fields=canonical,
        unknown_columns=unknown,
        identity_columns=identity,
        suggested_aliases=suggested,
        warnings=tuple(warnings),
    )

