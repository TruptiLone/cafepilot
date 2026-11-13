"""Serve the built React app (frontend/dist) from FastAPI, so one server runs the whole demo (FRONTEND_DIST).

/assets/* are Vite's hashed files. Any other path that isn't an API call gets the file at that path when
there is one (e.g. a favicon), else index.html, so links like /receiving?po=PO-123 open the right page.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

HASHED = {"Cache-Control": "public, max-age=31536000, immutable"}
INDEX = {"Cache-Control": "no-cache"}  # a new deploy shows up on the next load


class _HashedFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers.update(HASHED)
        return response


def mount_frontend(app: FastAPI, dist: Path) -> None:
    """Add after every API router: the catch-all route must come last."""
    root = dist.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError(f"FRONTEND_DIST has no index.html: {root}")
    if (root / "assets").is_dir():
        app.mount("/assets", _HashedFiles(directory=root / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def page(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (root / path).resolve()
        if path and candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers=INDEX)
