"""Dependency-free operator console for the fixloop service.

P1 can expose the console by importing ``router`` and calling
``app.include_router(router)``. Keeping the UI in its own router avoids
coupling the demo surface to the job runner.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse


STATIC_DIR = Path(__file__).resolve().parent / "static"

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=FileResponse)
def console() -> FileResponse:
    """Serve the live verification console."""
    return FileResponse(
        STATIC_DIR / "index.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/assets/fixloop.css", response_class=FileResponse)
def console_styles() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "fixloop.css",
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/assets/fixloop.js", response_class=FileResponse)
def console_script() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "fixloop.js",
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=3600"},
    )
