"""Shared offline fakes for the unit suite.

Two phases (18 and 20) drive services whose repositories only `list`, `add`
and `flush`, so both need a session that stores what it is given and answers
queries about it. One implementation, imported by both, so a change to how the
fake reads a where clause is a change everywhere at once rather than a drift
between two copies that both still pass.

What this leaves unverified is whether Postgres accepts the rows — the same
caveat as every other phase (D-006).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, Grouping


def matches(row: Any, clause: Any) -> bool:
    """Evaluate the equality clauses these queries use, against one row.

    The services only ever ask `=`, `AND`, `OR`, `IN` and `IS NULL`, so a small
    evaluator is enough — and it is what makes the fake able to answer "the
    request you already opened" instead of "nothing", which is the difference
    between testing a state machine and testing that it never reads.
    """
    if clause is None:
        return True
    if isinstance(clause, Grouping):
        # `(a OR b) AND tenant...` parenthesises the OR; the grouping is SQL
        # syntax, not a predicate.
        return matches(row, clause.element)
    if isinstance(clause, BooleanClauseList):
        results = [matches(row, part) for part in clause.clauses]
        return all(results) if clause.operator is operators.and_ else any(results)
    if isinstance(clause, BinaryExpression) and clause.operator is operators.eq:
        return getattr(row, clause.left.key, object()) == clause.right.value
    if isinstance(clause, BinaryExpression) and clause.operator is operators.in_op:
        return getattr(row, clause.left.key, object()) in clause.right.value
    if isinstance(clause, BinaryExpression) and clause.operator is operators.ilike_op:
        # `%needle%` against the row's string value, case-insensitively.
        pattern = str(clause.right.value)
        value = getattr(row, clause.left.key, None)
        if value is None:
            return False
        needle = pattern.strip("%")
        return needle in str(value).lower()
    if isinstance(clause, BinaryExpression) and clause.operator is operators.is_:
        # The tenant scoping adds `deleted_at IS NULL`.
        wanted = clause.right.value if hasattr(clause.right, "value") else None
        return getattr(row, clause.left.key, object()) is wanted
    raise AssertionError(f"unhandled clause in fake session: {clause!r}")


def fake_session() -> MagicMock:
    """A session that stores what it is given and answers queries about it.

    Reads run against the rows `add` has received, filtered by the statement's
    own where clause. That is what lets a test follow a proposal from its
    creation to its execution without a database.
    """
    session = MagicMock()
    session.rows: list[Any] = []

    def add(row: Any) -> Any:
        # Mimic the INSERT SQLAlchemy would do: the primary key default fires
        # at flush time, so a row the fake holds without one would be
        # indistinguishable from every other row to the queries below.
        if getattr(row, "id", None) is None:
            row.id = uuid4()
        session.rows.append(row)
        return row

    session.add = MagicMock(side_effect=add)
    session.flush = AsyncMock(return_value=None)
    # Phase 22 commits after writing its audit row; without this the service
    # awaited a MagicMock.
    session.commit = AsyncMock(return_value=None)
    session.rollback = AsyncMock(return_value=None)

    async def execute(statement: Any) -> MagicMock:
        entity = statement.column_descriptions[0]["entity"]
        clause = getattr(statement, "whereclause", None)
        matched = [
            row
            for row in session.rows
            if isinstance(row, entity) and matches(row, clause)
        ]
        matched = _window(matched, statement)
        result = MagicMock()
        result.scalars.return_value = result
        result.all.return_value = matched
        result.scalar_one_or_none.return_value = matched[0] if matched else None
        return result

    session.execute = AsyncMock(side_effect=execute)
    return session


def _window(rows: list[Any], statement: Any) -> list[Any]:
    """Apply the statement's LIMIT and OFFSET.

    A real repository paginates; a fake that ignored it returned every row, so a
    test asserting "the tool clamps to 20 rows" would have been asserting
    nothing. The clause attributes are private, which is the price of teaching
    the fake something only the database enforces otherwise.
    """
    offset = getattr(statement, "_offset_clause", None)
    limit = getattr(statement, "_limit_clause", None)
    start = getattr(offset, "value", None) if offset is not None else None
    size = getattr(limit, "value", None) if limit is not None else None
    if start:
        rows = rows[start:]
    if size is not None:
        rows = rows[:size]
    return rows


def written(session: MagicMock) -> list[Any]:
    """Every row the fake session has been asked to insert, in order."""
    return [call.args[0] for call in session.add.call_args_list]
