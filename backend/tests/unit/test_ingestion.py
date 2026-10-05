"""Ingestion tests (Phase 10, §49).

Everything here runs offline: the parsers, the security gate, schema detection and
the pipeline's pure half need no Postgres and no Redis. The scenarios are the ones
that produce support tickets -- a BOM gluing itself to the first header, a
duplicated `colour`/`color` column silently dropping one, a supplier "CSV" that is
secretly a binary, and the `=cmd()` cell in a price column.
"""

from __future__ import annotations

import types
from io import BytesIO

import httpx
import openpyxl
import pytest
from httpx import ASGITransport

from app.main import app
from app.services.ingestion import formats, pipeline, security
from app.services.ingestion.errors import IngestionError, IngestionErrorCode
from app.services.ingestion.schema import detect_schema, profile_column

_DEFAULTS = {
    "ingestion_allowed_extensions": [".csv", ".json", ".xlsx"],
    "ingestion_max_rows": 200_000,
    "ingestion_max_columns": 512,
    "ingestion_neutralize_formulas": True,
    "ingestion_inline_max_bytes": 5 * 1024 * 1024,
    "ingestion_execution": "inline",
    "ingestion_scanner": "none",
    "max_upload_mb": 100,
}


def patch(monkeypatch: pytest.MonkeyPatch, module: object, **overrides: object) -> None:
    """Point a module's `get_settings` at a fake, so limits are testable."""
    values = {**_DEFAULTS, **overrides}
    monkeypatch.setattr(module, "get_settings", lambda: types.SimpleNamespace(**values))


class TestFileSecurity:
    def test_supported_extension_passes(self) -> None:
        result = security.validate_file("supplier.csv", b"sku,col\nA1,red\n")
        assert result.extension == ".csv"
        assert result.scanner == "none"
        assert result.scan_detail and "not scanned" in result.scan_detail

    def test_unknown_extension_is_refused(self) -> None:
        with pytest.raises(IngestionError) as exc:
            security.validate_file("rules.pdf", b"%PDF-1.7")
        assert exc.value.code is IngestionErrorCode.UNSUPPORTED_FORMAT
        assert exc.value.recoverable is True

    def test_empty_file_is_refused(self) -> None:
        with pytest.raises(IngestionError) as exc:
            security.validate_file("empty.csv", b"")
        assert exc.value.code is IngestionErrorCode.EMPTY_FILE

    def test_oversize_file_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patch(monkeypatch, security, max_upload_mb=0)
        with pytest.raises(IngestionError) as exc:
            security.validate_file("big.csv", b"a")
        assert exc.value.code is IngestionErrorCode.FILE_TOO_LARGE

    def test_xlsx_must_be_a_zip_container(self) -> None:
        with pytest.raises(IngestionError) as exc:
            security.validate_file("sheet.xlsx", b"not really a zip")
        assert exc.value.code is IngestionErrorCode.SECURITY_REJECTED

    def test_binary_data_in_a_text_file_is_refused(self) -> None:
        with pytest.raises(IngestionError) as exc:
            security.validate_file("sneaky.csv", b"sku\x00\x01\x02")
        assert exc.value.code is IngestionErrorCode.SECURITY_REJECTED

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("=1+1", "'=1+1"),
            ("+cmd|'/c calc'!A0", "'+cmd|'/c calc'!A0"),
            ("@SUM(A1)", "'@SUM(A1)"),
            ("-3.50", "-3.50"),  # a real negative price, not a formula
            ("+2", "+2"),
            ("Navy Blue", "Navy Blue"),
        ],
    )
    def test_formula_neutralisation(self, raw: str, expected: str) -> None:
        assert security.neutralize_formula(raw) == expected

    def test_bom_does_not_leak_into_the_first_header(self) -> None:
        text, encoding = security.detect_encoding(b"\xef\xbb\xbfsku\nA1\n")
        assert encoding == "utf-8-sig"
        assert text.splitlines()[0] == "sku"

    @pytest.mark.parametrize(
        ("filename", "expected"),
        [("a.CSV", ".csv"), ("noext", ""), ("archive.tar.gz", ".gz"), ("x.JSON", ".json")],
    )
    def test_extension_extraction(self, filename: str, expected: str) -> None:
        assert security.extension_of(filename) == expected


def _xlsx_bytes(rows: list[list[object]]) -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


class TestFormats:
    def test_csv_headers_and_rows(self) -> None:
        parsed = formats.parse_file(".csv", b"SKU,Colour\nA1,Navy Blue\n")
        assert parsed.columns == ("SKU", "Colour")
        assert parsed.format == "csv"
        assert parsed.rows[0]["SKU"] == "A1"
        assert parsed.rows[0]["Colour"] == "Navy Blue"

    def test_csv_duplicate_headers_are_disambiguated(self) -> None:
        parsed = formats.parse_file(".csv", b"sku,sku\nA1,B2\n")
        assert parsed.columns == ("sku", "sku_2")

    def test_csv_blank_header_is_named(self) -> None:
        parsed = formats.parse_file(".csv", b"sku,\nA1,B2\n")
        assert parsed.columns == ("sku", "column_2")

    def test_csv_rows_are_truncated_at_the_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch(monkeypatch, formats, ingestion_max_rows=2)
        parsed = formats.parse_file(".csv", b"sku\nA\nB\nC\n")
        assert parsed.row_count == 2
        assert parsed.truncated is True
        assert any("first 2 rows" in warning for warning in parsed.warnings)

    def test_column_cap_is_enforced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        patch(monkeypatch, formats, ingestion_max_columns=1)
        with pytest.raises(IngestionError) as exc:
            formats.parse_file(".csv", b"a,b\n1,2\n")
        assert exc.value.code is IngestionErrorCode.STRUCTURAL_LIMIT_EXCEEDED

    def test_csv_formula_cell_is_neutralised(self) -> None:
        parsed = formats.parse_file(".csv", b"sku,price\nA1,=cmd()\n")
        assert parsed.rows[0]["price"] == "'=cmd()"

    def test_json_top_level_list(self) -> None:
        parsed = formats.parse_file(".json", b'[{"sku": "A1"}, {"sku": "A2"}]')
        assert parsed.row_count == 2
        assert parsed.columns == ("sku",)

    def test_json_wrapped_envelope(self) -> None:
        parsed = formats.parse_file(".json", b'{"items": [{"sku": "A1"}]}')
        assert parsed.row_count == 1

    def test_json_single_object_is_one_row(self) -> None:
        parsed = formats.parse_file(".json", b'{"sku": "A1", "gtin": "0123"}')
        assert parsed.row_count == 1
        assert set(parsed.columns) == {"sku", "gtin"}

    def test_json_missing_key_becomes_null(self) -> None:
        parsed = formats.parse_file(
            ".json", b'[{"sku":"A1","gtin":"1"},{"sku":"A2"}]'
        )
        assert parsed.rows[1]["gtin"] is None

    def test_invalid_json_is_rejected(self) -> None:
        with pytest.raises(IngestionError) as exc:
            formats.parse_file(".json", b"{not json}")
        assert exc.value.code is IngestionErrorCode.MALFORMED_FILE

    def test_xlsx_is_parsed(self) -> None:
        data = _xlsx_bytes([["sku", "colour"], ["A1", "Navy Blue"]])
        parsed = formats.parse_file(".xlsx", data)
        assert parsed.format == "xlsx"
        assert parsed.rows[0]["sku"] == "A1"

    def test_xlsx_that_is_not_a_workbook_is_rejected(self) -> None:
        with pytest.raises(IngestionError) as exc:
            formats.parse_file(".xlsx", b"PK\x03\x04garbage")
        assert exc.value.code is IngestionErrorCode.MALFORMED_FILE


class TestSchemaDetection:
    def test_integer_inference_and_null_rate(self) -> None:
        profile = profile_column("qty", ["5", "7", None])
        assert profile.inferred_type == "integer"
        assert profile.null_rate == pytest.approx(1 / 3)
        assert profile.distinct_count == 2

    def test_leading_zero_is_a_string_not_an_integer(self) -> None:
        assert profile_column("gtin", ["007", "008"]).inferred_type == "string"

    def test_mixed_types_are_reported_as_mixed(self) -> None:
        assert profile_column("x", ["1", "abc"]).inferred_type == "mixed"

    def test_canonical_mapping_and_unknowns(self) -> None:
        rows = [{"Product Colour": "Navy", "vendorNote": "x"}]
        detection = detect_schema(rows, ("Product Colour", "vendorNote"))
        by_name = {column.name: column for column in detection.columns}
        assert by_name["Product Colour"].canonical == "color"
        assert by_name["vendorNote"].canonical is None
        assert detection.unknown_columns == ("vendorNote",)

    def test_identity_column_is_detected(self) -> None:
        detection = detect_schema([{"gtin": "0123"}], ("gtin",))
        assert "gtin" in detection.identity_columns

    def test_missing_identity_warns(self) -> None:
        detection = detect_schema([{"nickname": "x"}], ("nickname",))
        assert any("identity column" in warning for warning in detection.warnings)

    def test_unique_column_is_flagged(self) -> None:
        detection = detect_schema([{"sku": "A"}, {"sku": "B"}], ("sku",))
        assert detection.columns[0].is_unique is True


class TestPipeline:
    def test_plan_execution_defaults_to_inline(self) -> None:
        assert pipeline.plan_execution(10) == "inline"
        assert pipeline.plan_execution(10_000_000) == "inline"

    def test_plan_execution_queues_large_files_in_dramatiq_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patch(
            monkeypatch,
            pipeline,
            ingestion_execution="dramatiq",
            ingestion_inline_max_bytes=1024,
        )
        assert pipeline.plan_execution(2048) == "queued"
        assert pipeline.plan_execution(512) == "inline"

    def test_prepare_builds_records_keyed_by_identity(self) -> None:
        csv = b"gtin,title\n0123456789,Widget\n0987654321,Gadget\n"
        prepared = pipeline.prepare("feed.csv", csv)
        assert [r.external_id for r in prepared.records] == [
            "0123456789",
            "0987654321",
        ]
        assert prepared.schema.row_count == 2

    def test_prepare_suffixes_duplicate_external_ids(self) -> None:
        csv = b"sku,title\nA1,One\nA1,Two\n"
        prepared = pipeline.prepare("feed.csv", csv)
        assert [r.external_id for r in prepared.records] == ["A1", "A1#2"]

    def test_prepare_falls_back_to_row_number(self) -> None:
        csv = b"title\nWidget\nGadget\n"
        prepared = pipeline.prepare("feed.csv", csv)
        assert [r.external_id for r in prepared.records] == ["row-1", "row-2"]


class TestIngestionApi:
    async def test_routes_are_in_the_spec(self) -> None:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            paths = (await client.get("/openapi.json")).json()["paths"]
        assert "/api/v1/catalog-sources" in paths
        assert "/api/v1/catalog-sources/{source_id}/upload" in paths
        assert "/api/v1/catalog-sources/{source_id}/records" in paths

    async def test_listing_requires_a_session(self) -> None:
        """No cookie, no workspace: a 401, and never a 500.

        The authentication dependency runs before any database access, so this
        holds even with an unreachable Postgres.
        """
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/catalog-sources")
        assert response.status_code == 401


