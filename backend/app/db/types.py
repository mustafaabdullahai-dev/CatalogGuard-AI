"""A JSON column that accepts the domain's own types.

Several tables hold free-form values — `products.attributes`,
`conflicts.competing_values`, `change_proposals.current_value` — and the domain
is entitled to produce types `json.dumps` refuses. `Decimal` is the one that
bites: measure and currency normalization return it deliberately, because
`Decimal("10.0")` and `Decimal("10.00")` compare equal and `float` does not
preserve that.

Every such write failed with `TypeError: Object of type Decimal is not JSON
serializable`, and because a reconciliation commits its products, conflicts and
proposals together, one price in one supplier file lost the whole run rather
than one row. Three separate call sites needed the same guard before this
existed, which is the definition of a missing abstraction.

`JSONValue` is that abstraction, applied to the column rather than to the
callers:

- one definition, so a fourth site cannot forget it;
- the same DDL as `JSON`, so the generated migration does not change and no
  deployment is affected by the fix;
- conversion happens on bind only, and `json_safe` is idempotent, so reading a
  row and writing it back is a no-op.

The alternative — teaching every writer about `Decimal` — was rejected: it puts
a persistence concern in the middle of the reconciliation engine, and the next
type the domain grows would find the same seam again.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator

from app.db.json_values import json_safe


class JSONValue(TypeDecorator):  # type: ignore[type-arg]
    """`JSON`, with domain values converted on the way in.

    Renders as `JSON` in DDL. Use it wherever a column holds values the
    application computed rather than values a client sent.
    """

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:  # noqa: ARG002
        return json_safe(value)


__all__ = ["JSONValue", "json_safe"]