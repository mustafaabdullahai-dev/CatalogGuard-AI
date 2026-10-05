"""File validation and the security seam (§49).

§49 puts "file validation" and "virus/security check where applicable" ahead of
parsing, for an obvious reason: a parser is the most dangerous component in an
intake path, so every cheap check that can reject a file must run before it.

What is enforced here is deterministic and offline:

  - an extension allowlist, backed by a magic-byte check, because an extension
    is a claim about a file and not a fact about it;
  - a size cap, from `MAX_UPLOAD_MB`;
  - a refusal of binary content for formats that must be text (a NUL byte in a
    CSV is a mislabelled executable far more often than it is a valid file);
  - CSV formula neutralisation, because a catalog export opened in a
    spreadsheet is a formula-injection vector aimed at the reviewer.

The anti-malware step is a port with one honest adapter, mirroring
`app.services.notify.delivery`. No scanner is deployed, so the default reports
`none` and says so; it never returns a "clean" that implies a scan happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol, runtime_checkable

from app.core.config import get_settings
from app.services.ingestion.errors import IngestionError, IngestionErrorCode

# `.xlsx` is a ZIP container (OOXML). A file that claims to be Excel but is not
# a ZIP is either corrupt or deliberately mislabelled; both are refusals.
_XLSX_MAGIC = b"PK\x03\x04"

# Prefixes that a spreadsheet interprets as the start of a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@")

# Encodings tried in order. `utf-8-sig` first so a BOM-prefixed file -- the
# default output of Excel's "CSV UTF-8" -- does not leave a stray BOM glued to
# the first column name, which would make every header alias miss.
_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252")


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Outcome of the (optional) anti-malware step."""

    clean: bool
    engine: str
    findings: tuple[str, ...] = ()
    detail: str | None = None


@runtime_checkable
class FileScanner(Protocol):
    """Scans bytes before they reach a parser.

    Implementations must not raise: a scanner that fails is reported as a
    non-clean result, so a broken engine cannot be mistaken for a pass.
    """

    engine: str

    def scan(self, *, filename: str, content: bytes) -> ScanResult:
        ...


class NoScanner:
    """Default adapter: nothing is scanned, and the result says so.

    Named for what it does. It returns `clean=True` because there is nothing to
    reject on, but `detail` states plainly that no engine ran, so the value can
    never be read as "this file was checked".
    """

    engine = "none"

    def scan(self, *, filename: str, content: bytes) -> ScanResult:  # noqa: ARG002
        return ScanResult(
            clean=True,
            engine=self.engine,
            detail="no anti-malware engine is configured; content is not scanned",
        )


def get_scanner() -> FileScanner:
    """The configured scanner.

    Raising on an unknown value rather than falling back to the no-op: a
    deployment that believes it is scanning and is not is the failure this port
    exists to make impossible.
    """
    settings = get_settings()
    if settings.ingestion_scanner == "none":
        return NoScanner()
    raise ValueError(f"unsupported INGESTION_SCANNER: {settings.ingestion_scanner}")


@dataclass(frozen=True, slots=True)
class ValidatedFile:
    """A file that passed every deterministic check."""

    filename: str
    extension: str
    size_bytes: int
    scanner: str
    scan_detail: str | None = None


def extension_of(filename: str) -> str:
    """Lower-case extension including the dot, or an empty string.

    Splits on the last dot only, so `q1.2026.prices.csv` is a CSV. A name with
    no dot has no extension and will fail the allowlist.
    """
    if "." not in filename:
        return ""
    _, _, suffix = filename.rpartition(".")
    return f".{suffix}".lower() if suffix else ""


def max_upload_bytes() -> int:
    return get_settings().max_upload_mb * 1024 * 1024


def detect_encoding(content: bytes) -> tuple[str, str]:
    """Decode text content, returning `(text, encoding)`.

    Tries UTF-8 (with and without BOM) then cp1252, which covers the curly
    quotes and en-dashes that arrive from Word and Windows exports. If nothing
    decodes, the file is not text and this is an error rather than a fallback to
    `errors="replace"`: silently replacing bytes would corrupt identifiers, and a
    corrupted GTIN looks like a real value.
    """
    for encoding in _TEXT_ENCODINGS:
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise IngestionError(
        code=IngestionErrorCode.ENCODING_UNDETECTABLE,
        message=(
            "the file could not be decoded as text; it may be binary or use an "
            "unsupported encoding"
        ),
        details={"tried": list(_TEXT_ENCODINGS)},
        recoverable=True,
    )


def neutralize_formula(value: str) -> str:
    """Defuse a CSV cell that a spreadsheet would treat as a formula.

    A leading `'` is the conventional neutraliser: spreadsheets render the cell
    literally and do not evaluate it.

    A bare signed number (`-5`, `+3.20`) is left alone. Those are legitimate
    values in a price or adjustment column, and prefixing them would corrupt
    real data to defend against a threat that requires a non-numeric tail.
    """
    if not value or value[0] not in _FORMULA_PREFIXES:
        return value
    try:
        Decimal(value)
    except (InvalidOperation, ValueError):
        return f"'{value}"
    return value


def validate_file(filename: str, content: bytes) -> ValidatedFile:
    """Run every deterministic check and either pass the file or reject it.

    Order matters: cheap and unambiguous rejections (empty, wrong extension,
    oversize) run before the scanner, so an operator sees the most specific
    reason rather than a generic security refusal.
    """
    settings = get_settings()

    if not content:
        raise IngestionError(
            code=IngestionErrorCode.EMPTY_FILE,
            message="the uploaded file is empty",
            recoverable=True,
        )

    extension = extension_of(filename)
    allowed = list(settings.ingestion_allowed_extensions)
    if extension not in allowed:
        raise IngestionError(
            code=IngestionErrorCode.UNSUPPORTED_FORMAT,
            message=(
                f"files of type {extension or '(none)'} are not accepted; "
                f"supported types are {', '.join(allowed)}"
            ),
            details={"extension": extension, "allowed": allowed},
            recoverable=True,
        )

    if len(content) > max_upload_bytes():
        raise IngestionError(
            code=IngestionErrorCode.FILE_TOO_LARGE,
            message=f"the file exceeds the {settings.max_upload_mb} MB limit",
            details={"size_bytes": len(content), "limit_bytes": max_upload_bytes()},
            recoverable=True,
        )

    if extension == ".xlsx":
        if not content.startswith(_XLSX_MAGIC):
            raise IngestionError(
                code=IngestionErrorCode.SECURITY_REJECTED,
                message="the file is named .xlsx but is not a valid Excel container",
                recoverable=True,
            )
    elif b"\x00" in content:
        # NUL bytes are the cheapest reliable signal that a "text" file is
        # something else. A genuine CSV or JSON export never contains one.
        raise IngestionError(
            code=IngestionErrorCode.SECURITY_REJECTED,
            message="the file contains binary data but was uploaded as text",
            recoverable=True,
        )

    scan = get_scanner().scan(filename=filename, content=content)
    if not scan.clean:
        raise IngestionError(
            code=IngestionErrorCode.SECURITY_REJECTED,
            message="the file was rejected by the security scan",
            details={"engine": scan.engine, "findings": list(scan.findings)},
            recoverable=False,
        )

    return ValidatedFile(
        filename=filename,
        extension=extension,
        size_bytes=len(content),
        scanner=scan.engine,
        scan_detail=scan.detail,
    )

