"""Values crossing into a JSON column.

The domain's types are its own: `normalize_measure` returns `Decimal` because
`Decimal("10.0")` and `Decimal("10.00")` compare equal and `float` does not
preserve that. PostgreSQL's `json` column cannot store a `Decimal`, and three
separate write paths — products, conflicts, proposals — failed with
`TypeError: Object of type Decimal is not JSON serializable` before the wrapper
existed. One bad price in a supplier file lost a whole reconciliation.

These assert the conversion, and one assertion that it does *not* lose the
value: a price stored as a float reads back changed.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from app.db.json_values import json_safe
from app.db.types import JSONValue


class _Colour(StrEnum):
    SLATE = "slate blue"


class TestJsonSafe:
    def test_a_decimal_becomes_a_string_not_a_float(self) -> None:
        """`float` would turn "289.00" into 289.0, which is a different price."""

        assert json_safe(Decimal("289.00")) == "289.00"
        assert isinstance(json_safe(Decimal("289.00")), str)

    def test_json_native_types_pass_through_untouched(self) -> None:
        for value in ("text", 1, 1.5, True, None):
            assert json_safe(value) == value

    def test_nested_containers_are_converted_all_the_way_down(self) -> None:
        converted = json_safe(
            {"measure": {"weight": Decimal("1.5")}, "history": [Decimal("2.00")]}
        )

        assert converted == {"measure": {"weight": "1.5"}, "history": ["2.00"]}
        json.dumps(converted)  # the point: this must not raise

    def test_sets_and_tuples_become_lists(self) -> None:
        assert json_safe({Decimal("1")}) == ["1"]
        assert json_safe((1, Decimal("2"))) == [1, "2"]

    def test_datetimes_and_uuids_become_strings(self) -> None:
        moment = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
        identifier = UUID("00000000-0000-0000-0000-000000000001")

        assert json_safe(moment) == "2026-01-02T03:04:00+00:00"
        assert json_safe(date(2026, 1, 2)) == "2026-01-02"
        assert json_safe(identifier) == "00000000-0000-0000-0000-000000000001"

    def test_an_enum_becomes_its_value(self) -> None:
        assert json_safe(_Colour.SLATE) == "slate blue"

    def test_bytes_are_decoded_rather_than_repr_ed(self) -> None:
        assert json_safe(b"navy") == "navy"

    def test_an_unanticipated_type_is_stringified_not_rejected(self) -> None:
        """A supplier file is external input; an unknown value is not a reason
        to lose the rest of the row."""

        class Opaque:
            def __str__(self) -> str:
                return "opaque"

        assert json_safe(Opaque()) == "opaque"

    def test_the_conversion_is_idempotent(self) -> None:
        """Reading a row and writing it back must not change it again."""

        once = json_safe({"price": Decimal("10.00")})

        assert json_safe(once) == once


class TestTheJsonColumnWrapper:
    def test_it_renders_as_plain_json_in_ddl(self) -> None:
        """The fix must not change any existing schema.

        A wrapper that altered the column type would require a migration on
        every deployment for a bug fix, and the migration-parity test would
        rightly refuse it.
        """

        rendered = str(
            JSONValue().compile(dialect=postgresql.dialect())
        )

        assert "JSON" in rendered.upper()
        assert "JSONValue" not in rendered

    def test_it_converts_on_bind(self) -> None:
        dialect = postgresql.dialect()

        assert JSONValue().process_bind_param({"p": Decimal("1.5")}, dialect) == {"p": "1.5"}

    @pytest.mark.parametrize("value", [None, {}, [], "text", 3])
    def test_a_value_with_nothing_to_convert_is_unchanged(self, value: object) -> None:
        assert JSONValue().process_bind_param(value, None) == value