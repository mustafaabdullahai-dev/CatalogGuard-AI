"""Values on their way into a JSON column.

Several tables hold free-form values — `products.attributes`,
`conflicts.competing_values`, `source_records.raw_data` — and the domain is
entitled to produce types that `json.dumps` refuses. `Decimal` is the one that
actually bites: measure and currency normalization return it deliberately,
because `Decimal("10.0")` and `Decimal("10.00")` compare equal and `float` does
not preserve that.

Every such write failed with `TypeError: Object of type Decimal is not JSON
serializable`, and because canonicalization commits its products and the
reconciler its conflicts in the same operation, one bad value in a supplier file
lost the whole import rather than one row. Both were found in Phase 25 by
running a real CSV through the real pipeline.

The rule this module encodes: **the domain keeps its types, and the JSON
boundary converts them.** Comparisons happen before conversion, so exactness is
not lost; and `Decimal` becomes a string, which is what ingestion already stores
(`_coerce` in `services/ingestion/formats.py`), so a raw record and the
canonical product derived from it keep the same persisted shape.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

#: Types that survive a JSON round trip unchanged.
_JSON_NATIVE = (str, int, float, bool)


def json_safe(value: Any) -> Any:
    """Convert `value` into something `json.dumps` will accept.

    Recurses through containers. A type with no JSON form and no obvious
    string form — a set, a tuple of models, a driver-specific object — is
    stringified rather than rejected: a supplier file is external input, and a
    value nobody anticipated is a reason to keep the rest of the row, not to
    lose it.
    """
    if value is None or isinstance(value, _JSON_NATIVE):
        return value
    if isinstance(value, Decimal):
        # `str` and not `float`: "289.00" must stay "289.00". A price that
        # reads back as 289.0 is a price that has silently changed.
        return str(value)
    if isinstance(value, Enum):
        return json_safe(value.value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes | bytearray):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [json_safe(item) for item in value]
    return str(value)