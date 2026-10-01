"""Brand assets served by the API.

The logo lives in the frontend at build time; this copy exists so the API's
own docs and health surfaces are branded without depending on the frontend
being built or running.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["brand"])

# `app/api/v1/static` does not exist; the assets live one level up, beside the
# rest of the package. Pointing at the wrong directory is a 404 that looks
# exactly like a missing file, which is how it survived as long as it did.
_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


@router.get("/brand/logo.png", include_in_schema=False)
async def get_logo() -> FileResponse:
    """Serve the CatalogGuard logo."""
    path = _STATIC_DIR / "logo.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Logo asset not found")
    return FileResponse(path, media_type="image/png")