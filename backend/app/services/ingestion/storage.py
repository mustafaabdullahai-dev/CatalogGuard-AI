"""Upload storage for the async ingestion path (§28).

Used only when `INGESTION_EXECUTION` is `dramatiq` and a file is too large to
parse inline. The file is written under `UPLOAD_DIR`, keyed by workspace, so a
worker can find it by path.

The filename is never used to build the path. A client-supplied name like
`../../etc/passwd` or one containing a NUL cannot escape the directory because
the stored name is a fresh UUID plus the already-validated extension; the
original name travels separately, as data.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from app.core.config import get_settings


def upload_root() -> Path:
    return Path(get_settings().upload_dir).expanduser().resolve()


def store_upload(workspace_id: UUID | str, extension: str, content: bytes) -> Path:
    """Write `content` to a per-workspace directory and return its path."""
    directory = upload_root() / str(workspace_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid4().hex}{extension}"
    path.write_bytes(content)
    return path


def load_upload(path: str | Path) -> bytes:
    return Path(path).read_bytes()
