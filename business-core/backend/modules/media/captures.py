"""Hackathon photo intake and polling gallery, backed by the existing media store."""
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import fcntl
import json
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, Response

from backend.db.config import Settings
from backend.modules.media.processing import Analysis, load_analysis, process_photo
from backend.modules.media.router import MediaOut, authorize, read_metadata, upload

router = APIRouter(tags=["glasses photos"])


class PhotoOut(MediaOut):
    processing: Analysis


def photo_out(record: MediaOut, settings: Settings) -> PhotoOut:
    return PhotoOut(**record.model_dump(), processing=load_analysis(settings.media_storage_dir / str(record.id)))


def paired_photo_path(settings: Settings, delivery_id: UUID, role: str) -> Path:
    return Path(os.path.expanduser(str(settings.barista_shared_photos_dir))) / str(delivery_id) / f"{role}.jpg"


def update_delivery_pair(settings: Settings, delivery_id: UUID, role: str, record: MediaOut) -> dict:
    folder = paired_photo_path(settings, delivery_id, role).parent
    folder.mkdir(parents=True, exist_ok=True)
    lock_path = folder / ".manifest.lock"
    manifest_path = folder / "manifest.json"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            try:
                manifest = json.loads(manifest_path.read_text())
            except FileNotFoundError:
                manifest = {"delivery_id": str(delivery_id), "status": "waiting_for_images", "images": {}}
            prior = manifest.get("images", {}).get(role)
            if manifest.get("status") in {"processing", "complete"} and prior and prior.get("capture_id") != str(record.id):
                raise HTTPException(409, "This delivery pair is already processing or complete.")
            source = settings.media_storage_dir / str(record.id) / "capture.jpg"
            photo = paired_photo_path(settings, delivery_id, role)
            temp_photo = folder / f".{role}-{record.id}.tmp"
            shutil.copyfile(source, temp_photo)
            os.replace(temp_photo, photo)
            manifest["images"][role] = {"capture_id": str(record.id), "captured_at": record.captured_at.isoformat()}
            manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
            if all(key in manifest["images"] for key in ("receipt", "delivery")) and manifest["status"] != "complete":
                manifest["status"] = "ready"
                manifest.pop("error", None)
            temporary = folder / ".manifest.tmp"
            temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True))
            os.replace(temporary, manifest_path)
            return manifest
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@router.post("/upload", response_model=PhotoOut, status_code=201)
async def upload_photo(
    request: Request, response: Response, background: BackgroundTasks,
    settings: Annotated[Settings, Depends(authorize)],
    x_capture_id: Annotated[UUID | None, Header()] = None,
    x_captured_at: Annotated[datetime | None, Header()] = None,
    x_delivery_id: Annotated[UUID | None, Header()] = None,
    x_image_role: Annotated[Literal["receipt", "delivery"] | None, Header()] = None,
) -> PhotoOut:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "image/jpeg":
        raise HTTPException(415, "Send raw JPEG image bytes with Content-Type: image/jpeg.")
    if (x_delivery_id is None) != (x_image_role is None):
        raise HTTPException(422, "Send both X-Delivery-ID and X-Image-Role for paired receiving photos.")
    record = await upload(request, response, settings, x_capture_id or uuid4(),
                          x_captured_at or datetime.now(timezone.utc))
    if x_delivery_id and x_image_role:
        update_delivery_pair(settings, x_delivery_id, x_image_role, record)
        shared_dir = Path(os.path.expanduser(str(settings.barista_shared_photos_dir)))
    else:
        shared_dir = Path(os.path.expanduser(str(settings.barista_shared_photos_dir)))
        source = settings.media_storage_dir / str(record.id) / "capture.jpg"
        shared_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{record.captured_at.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}_{record.id}.jpg"
        temporary = shared_dir / f".{filename}.tmp"
        shutil.copyfile(source, temporary)
        os.replace(temporary, shared_dir / filename)
    if x_delivery_id is None:
        background.add_task(process_photo, record.id, settings)
    return photo_out(record, settings)


@router.get("/photos", response_model=list[PhotoOut])
def photos(settings: Annotated[Settings, Depends(authorize)],
           limit: Annotated[int, Query(ge=1, le=100)] = 30) -> list[PhotoOut]:
    if not settings.media_storage_dir.exists():
        return []
    records = []
    for directory in settings.media_storage_dir.iterdir():
        try:
            UUID(directory.name)
        except ValueError:
            continue
        if not directory.is_dir():
            continue
        try:
            record = read_metadata(directory)
        except HTTPException as error:
            if error.status_code == 404:
                continue
            raise
        if record.kind == "photo":
            records.append(record)
    records.sort(key=lambda r: r.received_at, reverse=True)
    return [photo_out(record, settings) for record in records[:limit]]


@router.post("/photos/{capture_id}/process", response_model=PhotoOut, status_code=202)
def retry_processing(capture_id: UUID, background: BackgroundTasks,
                     settings: Annotated[Settings, Depends(authorize)]) -> PhotoOut:
    record = read_metadata(settings.media_storage_dir / str(capture_id))
    if record.kind != "photo":
        raise HTTPException(415, "Only photos can be sent to the image reader.")
    background.add_task(process_photo, capture_id, settings, True)
    return photo_out(record, settings)
