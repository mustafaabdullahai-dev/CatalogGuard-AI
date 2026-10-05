"""Format parsers (§49): CSV, JSON, Excel.

Every parser returns the same `ParsedFile`, so the pipeline never branches on
format again after this module. The row and column caps are applied *while*
parsing rather than after, so a hostile or broken file cannot allocate an
unbounded list before the limit is noticed. Hitting the row cap sets
`truncated` and adds a warning instead of silently shortening the file.

XML and PDF are absent on purpose. §49 lists them, but a parser is the most
dangerous component in an intake path and neither `defusedxml` nor a PDF
extractor is installed. Accepting a file nothing here can read would produce a
source with zero rows and no explanation; refusing the extension at the
security gate is the honest behaviour until the parser decision is made.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO, StringIO
from typing import Any

import openpyxl

from app.core.config import get_settings
from app.services.ingestion.errors import IngestionError, IngestionErrorCode
from app.services.ingestion.security import detect_encoding, neutralize_formula

# JSON envelopes seen in the wild. A top-level list is preferred; when a feed
# wraps its rows, look under one of these keys before giving up.
_JSON_ROW_KEYS = ("items", "records", "products", "data", "rows", "results")


@dataclass(frozen=True, slots=True)
class ParsedFile:
    """A file reduced to columns and rows, before any canonical mapping."""

    format: str
    encoding: str | None
    columns: tuple[str, ...]
    rows: tuple[dict[str, object], ...]
    row_count: int
    truncated: bool
    warnings: tuple[str, ...] = ()


def parse_file(extension: str, content: bytes) -> ParsedFile:
    """Dispatch to the parser for `extension`. Pure: bytes in, rows out."""
    settings = get_settings()
    row_limit = settings.ingestion_max_rows
    column_limit = settings.ingestion_max_columns
    neutralize = settings.ingestion_neutralize_formulas

    if extension == ".csv":
        text, encoding = detect_encoding(content)
        return _parse_csv(
            text,
            encoding,
            row_limit=row_limit,
            column_limit=column_limit,
            neutralize=neutralize,
        )
    if extension == ".json":
        text, encoding = detect_encoding(content)
        return _parse_json(
            text, encoding, row_limit=row_limit, column_limit=column_limit
        )
    if extension == ".xlsx":
        return _parse_xlsx(content, row_limit=row_limit, column_limit=column_limit)
    raise IngestionError(
        code=IngestionErrorCode.UNSUPPORTED_FORMAT,
        message=f"no parser is registered for {extension or '(no extension)'}",
        details={"extension": extension},
        recoverable=True,
    )


def _clean_headers(raw: list[Any]) -> tuple[str, ...]:
    """Normalise a header row: strip, name blanks, de-duplicate.

    A blank header becomes `column_N` and a repeated header gains a numeric
    suffix. Leaving duplicates would silently drop one of the two columns when
    the row is turned into a dict, which is exactly the kind of quiet data loss
    the rest of this pipeline is built to avoid.
    """
    headers: list[str] = []
    counts: dict[str, int] = {}
    for index, value in enumerate(raw):
        name = str(value).strip() if value is not None else ""
        if not name:
            name = f"column_{index + 1}"
        if name in counts:
            counts[name] += 1
            name = f"{name}_{counts[name]}"
        counts.setdefault(name, 1)
        headers.append(name)
    return tuple(headers)


def _coerce(value: Any) -> object:
    """Reduce a cell to a JSON-storable scalar.

    Nested objects and lists are serialised as JSON rather than dropped: a
    supplier's `dimensions: {l,w,h}` is data worth keeping, and the schema
    detector can still reason about the resulting string. Dates become ISO
    strings so the registry is timezone-unambiguous.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    text = str(value).strip()
    return text or None


def _assemble(
    headers: tuple[str, ...],
    data: Any,
    *,
    fmt: str,
    encoding: str | None,
    row_limit: int,
    neutralize: bool,
) -> ParsedFile:
    """Turn an iterable of cell sequences into `ParsedFile` rows, honouring caps."""
    rows: list[dict[str, object]] = []
    truncated = False
    ragged = False
    for raw_row in data:
        if len(rows) >= row_limit:
            truncated = True
            break
        cells = list(raw_row)
        if len(cells) != len(headers):
            ragged = True
        if len(cells) < len(headers):
            cells = [*cells, *([None] * (len(headers) - len(cells)))]
        elif len(cells) > len(headers):
            cells = cells[: len(headers)]
        row: dict[str, object] = {}
        for name, value in zip(headers, cells, strict=True):
            coerced = _coerce(value)
            if neutralize and isinstance(coerced, str):
                coerced = neutralize_formula(coerced)
            row[name] = coerced
        rows.append(row)

    warnings: list[str] = []
    if truncated:
        warnings.append(f"only the first {row_limit} rows were read")
    if ragged:
        warnings.append("some rows had a different number of columns than the header")
    return ParsedFile(
        format=fmt,
        encoding=encoding,
        columns=headers,
        rows=tuple(rows),
        row_count=len(rows),
        truncated=truncated,
        warnings=tuple(warnings),
    )


def _guard_column_limit(headers: tuple[str, ...], column_limit: int) -> None:
    if len(headers) > column_limit:
        raise IngestionError(
            code=IngestionErrorCode.STRUCTURAL_LIMIT_EXCEEDED,
            message=(
                f"the file has {len(headers)} columns, above the {column_limit} "
                "column limit"
            ),
            details={"columns": len(headers), "limit": column_limit},
            recoverable=False,
        )


def _parse_csv(
    text: str,
    encoding: str,
    *,
    row_limit: int,
    column_limit: int,
    neutralize: bool,
) -> ParsedFile:
    reader = csv.reader(StringIO(text))
    try:
        header_row = next(reader)
    except StopIteration as exc:
        raise IngestionError(
            code=IngestionErrorCode.MALFORMED_FILE,
            message="the CSV file has no header row",
            recoverable=True,
        ) from exc
    headers = _clean_headers(list(header_row))
    _guard_column_limit(headers, column_limit)
    return _assemble(
        headers,
        reader,
        fmt="csv",
        encoding=encoding,
        row_limit=row_limit,
        neutralize=neutralize,
    )


def _extract_json_rows(payload: Any) -> list[Any] | None:
    """Find the row list inside a JSON payload, or None for a single object."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in _JSON_ROW_KEYS:
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return candidate
    return None


def _headers_from_keys(rows: list[Any]) -> tuple[str, ...]:
    """Union of keys across records, in first-seen order."""
    order: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                order.append(key)
    return tuple(order)


def _parse_json(
    text: str, encoding: str, *, row_limit: int, column_limit: int
) -> ParsedFile:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IngestionError(
            code=IngestionErrorCode.MALFORMED_FILE,
            message=f"the JSON is not valid: {exc.msg} (line {exc.lineno})",
            details={"line": exc.lineno, "column": exc.colno},
            recoverable=True,
        ) from exc

    raw_rows = _extract_json_rows(payload)
    if raw_rows is None:
        # A single object is one product. Wrapping it here keeps every downstream
        # step operating on a list, so nothing special-cases the shape.
        raw_rows = [payload]
    if not all(isinstance(row, dict) for row in raw_rows):
        raise IngestionError(
            code=IngestionErrorCode.MALFORMED_FILE,
            message="every JSON record must be an object",
            recoverable=True,
        )

    headers = _headers_from_keys(raw_rows)
    _guard_column_limit(headers, column_limit)
    # Order each row by the header list so a key missing from a record becomes an
    # explicit null rather than a shorter row.
    ordered = ([row.get(name) for name in headers] for row in raw_rows)
    return _assemble(
        headers,
        ordered,
        fmt="json",
        encoding=encoding,
        row_limit=row_limit,
        neutralize=False,
    )


def _parse_xlsx(content: bytes, *, row_limit: int, column_limit: int) -> ParsedFile:
    try:
        workbook = openpyxl.load_workbook(
            BytesIO(content), read_only=True, data_only=True
        )
    except Exception as exc:  # noqa: BLE001 - openpyxl raises a wide range
        raise IngestionError(
            code=IngestionErrorCode.MALFORMED_FILE,
            message="the Excel workbook could not be opened",
            details={"error": type(exc).__name__},
            recoverable=True,
        ) from exc
    try:
        sheet = workbook.active
        iterator = sheet.iter_rows(values_only=True)
        try:
            header_row = next(iterator)
        except StopIteration as exc:
            raise IngestionError(
                code=IngestionErrorCode.MALFORMED_FILE,
                message="the Excel workbook has no rows",
                recoverable=True,
            ) from exc
        headers = _clean_headers(list(header_row))
        _guard_column_limit(headers, column_limit)
        # `data_only=True` reads cached formula results; openpyxl never evaluates
        # a formula, which is what keeps a spreadsheet from running code here.
        return _assemble(
            headers,
            iterator,
            fmt="xlsx",
            encoding=None,
            row_limit=row_limit,
            neutralize=False,
        )
    finally:
        workbook.close()


