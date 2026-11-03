"""Receive files without base64 or multipart buffering; publish only complete uploads."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Annotated, Literal
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from backend.db.config import Settings, get_settings

router = APIRouter(tags=["media"])
bearer = HTTPBearer(auto_error=False)
FORMATS = {"image/jpeg": ("photo", ".jpg"), "video/mp4": ("video", ".mp4")}


def authorize(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Settings:
    expected = settings.media_upload_token.get_secret_value().strip() if settings.media_upload_token else ""
    if not expected:
        raise HTTPException(503, "Media uploads are not configured. Set MEDIA_UPLOAD_TOKEN on the service.")
    if not credentials or not hmac.compare_digest(credentials.credentials.encode(), expected.encode()):
        raise HTTPException(401, "Invalid media service token.", headers={"WWW-Authenticate": "Bearer"})
    return settings


class MediaOut(BaseModel):
    id: UUID
    kind: Literal["photo", "video"]
    content_type: str
    size_bytes: int
    sha256: str
    captured_at: datetime
    received_at: datetime
    source: Literal["meta-glasses", "mock-glasses", "test"] = "meta-glasses"
    content_url: str


def read_metadata(directory: Path) -> MediaOut:
    try:
        return MediaOut.model_validate_json((directory / "metadata.json").read_text())
    except FileNotFoundError:
        raise HTTPException(404, "Capture not found.") from None


@router.get("/status")
def status(settings: Annotated[Settings, Depends(authorize)]) -> dict:
    return {"status": "ok", "service": "cafepilot-media", "version": 1,
            "max_upload_bytes": settings.media_max_upload_bytes, "content_types": list(FORMATS)}


@router.post("", response_model=MediaOut, status_code=201)
async def upload(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(authorize)],
    x_capture_id: Annotated[UUID, Header()],
    x_captured_at: Annotated[datetime, Header()],
) -> MediaOut:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in FORMATS:
        raise HTTPException(415, "Send image/jpeg or video/mp4 as the request body.")
    if x_captured_at.utcoffset() is None:
        raise HTTPException(422, "X-Captured-At must include a timezone offset.")
    source = request.headers.get("x-capture-source", "meta-glasses")
    if source not in {"meta-glasses", "mock-glasses", "test"}:
        raise HTTPException(422, "Unsupported capture source.")
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) < 0:
                raise ValueError
            if int(length) > settings.media_max_upload_bytes:
                raise HTTPException(413, "Capture exceeds the service upload limit.")
        except ValueError:
            raise HTTPException(400, "Invalid Content-Length.") from None

    root = settings.media_storage_dir
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".upload-", dir=root))
    kind, extension = FORMATS[content_type]
    filename = "capture" + extension
    size = 0
    sha = hashlib.sha256()
    prefix = bytearray()
    try:
        async with await anyio.open_file(staging / filename, "wb") as target:
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.media_max_upload_bytes:
                    raise HTTPException(413, "Capture exceeds the service upload limit.")
                if len(prefix) < 32:
                    prefix.extend(chunk[:32 - len(prefix)])
                sha.update(chunk)
                await target.write(chunk)
        if size == 0:
            raise HTTPException(400, "Capture is empty.")
        if length is not None and size != int(length):
            raise HTTPException(400, "Incomplete capture body.")
        # Basic container signatures, not a claim that the whole media file decodes.
        if content_type == "image/jpeg" and not prefix.startswith(b"\xff\xd8\xff"):
            raise HTTPException(415, "The body is not a JPEG image.")
        if content_type == "video/mp4" and prefix[4:8] != b"ftyp":
            raise HTTPException(415, "The body is not an MP4 container.")
        record = MediaOut(
            id=x_capture_id, kind=kind, content_type=content_type, size_bytes=size,
            sha256=sha.hexdigest(), captured_at=x_captured_at,
            source=source,
            received_at=datetime.now(timezone.utc), content_url=f"/api/v1/media/{x_capture_id}/content",
        )
        (staging / "metadata.json").write_text(record.model_dump_json(indent=2))
        destination = root / str(x_capture_id)
        try:
            # A directory becomes visible with its metadata and content together.
            os.rename(staging, destination)
        except OSError:
            if not destination.is_dir():
                raise
            existing = read_metadata(destination)
            if (existing.sha256, existing.content_type, existing.captured_at, existing.source) != (
                record.sha256, record.content_type, record.captured_at, record.source
            ):
                raise HTTPException(409, "Capture ID already belongs to different content or metadata.") from None
            response.status_code = 200
            return existing
        return record
    finally:
        # Also runs after a disconnect, size failure, or cancelled request.
        shutil.rmtree(staging, ignore_errors=True)


@router.get("/{capture_id}", response_model=MediaOut)
def metadata(capture_id: UUID, settings: Annotated[Settings, Depends(authorize)]) -> MediaOut:
    return read_metadata(settings.media_storage_dir / str(capture_id))


@router.get("/{capture_id}/content")
def content(capture_id: UUID, settings: Annotated[Settings, Depends(authorize)]) -> FileResponse:
    directory = settings.media_storage_dir / str(capture_id)
    record = read_metadata(directory)
    extension = FORMATS[record.content_type][1]
    return FileResponse(directory / ("capture" + extension), media_type=record.content_type,
                        filename=str(capture_id) + extension)
