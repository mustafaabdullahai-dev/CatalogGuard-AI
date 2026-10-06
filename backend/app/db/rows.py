"""Row-count access for DML results.

`AsyncSession.execute` is typed as returning `Result`, which has no `rowcount`.
The concrete `CursorResult` returned for INSERT/UPDATE/DELETE does. Every caller
here is asking "how many rows did I actually change?", and that number decides
security behaviour — whether a single-use token was consumed, how many sessions a
reset revoked — so it is read through one named helper rather than
`getattr(result, "rowcount", 0)`, which would report zero for a successful
statement and quietly invert the decision.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy.engine import CursorResult, Result


def affected_rows(result: Result[Any]) -> int:
    """Rows changed by an INSERT, UPDATE or DELETE. Zero if none matched."""
    return int(cast("CursorResult[Any]", result).rowcount or 0)