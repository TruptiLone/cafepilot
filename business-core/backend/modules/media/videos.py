"""Video intake reuses the streaming uploader and the existing receiving image reader."""
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from backend.db.config import Settings
from backend.modules.agent.planner import create_planner_client
from backend.modules.media.router import MediaOut, authorize, read_metadata, upload
from backend.modules.receiving.vision import PhotoRead, VisionError, read_photo

router = APIRouter(tags=["glasses video"])
logger = logging.getLogger("cafepilot.video")
# Keep one clip's model calls together. File locks also prevent duplicate work
# when a phone retries a successful upload, including across server processes.
_worker_slot = threading.Semaphore(1)


class FrameResult(BaseModel):
    number: int
    offset_seconds: int
    result: PhotoRead | None = None
    latency_ms: int | None = None
    error: str | None = None


class VideoAnalysis(BaseModel):
    status: Literal["queued", "extracting", "processing", "complete", "partial", "failed"] = "queued"
    model: str | None = None
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    codec: str | None = None
    total_frames: int = 0
    completed_frames: int = 0
    frames: list[FrameResult] = Field(default_factory=list)
    error: str | None = None
    updated_at: datetime | None = None


class VideoOut(MediaOut):
    filename: str
    url: str
    processing: VideoAnalysis


def intake_settings(settings: Settings) -> Settings:
    # The existing uploader owns temporary files, limits, hashing and idempotency.
    # Its receipts stay private; a hard link publishes the timestamped MP4 below
    # without copying the video or consuming a second copy's disk space.
    return settings.model_copy(update={"media_storage_dir": settings.video_storage_dir / ".intake"})


def video_filename(record: MediaOut) -> str:
    stamp = record.received_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    return f"{stamp}_{record.id}.mp4"


def video_path(record: MediaOut, settings: Settings) -> Path:
    return settings.video_storage_dir / video_filename(record)


def result_path(record: MediaOut, settings: Settings) -> Path:
    return video_path(record, settings).with_suffix(".result.json")


def atomic_json(path: Path, value: BaseModel | dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
    try:
        temporary.write_text(value.model_dump_json(indent=2) if isinstance(value, BaseModel)
                             else json.dumps(value, indent=2))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def analysis_for(record: MediaOut, settings: Settings) -> VideoAnalysis:
    try:
        return VideoAnalysis.model_validate_json(result_path(record, settings).read_text())
    except FileNotFoundError:
        return VideoAnalysis()
    except ValidationError:
        return VideoAnalysis(status="failed", error="The saved result could not be read. Retry processing.")


def video_out(record: MediaOut, settings: Settings) -> VideoOut:
    url = f"/api/videos/{record.id}/content"
    return VideoOut(**record.model_dump(exclude={"content_url"}), content_url=url, url=url,
                    filename=video_filename(record), processing=analysis_for(record, settings))


def load_video(capture_id: UUID, settings: Settings) -> MediaOut:
    record = read_metadata(intake_settings(settings).media_storage_dir / str(capture_id))
    if record.kind != "video" or not video_path(record, settings).is_file():
        raise HTTPException(404, "Video not found.")
    return record


@router.post("/upload-video", response_model=VideoOut, status_code=201)
async def upload_video(
    request: Request, response: Response, background: BackgroundTasks,
    settings: Annotated[Settings, Depends(authorize)],
    x_capture_id: Annotated[UUID | None, Header()] = None,
    x_captured_at: Annotated[datetime | None, Header()] = None,
) -> VideoOut:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "video/mp4":
        raise HTTPException(415, "Send raw MP4 bytes with Content-Type: video/mp4.")
    record = await upload(request, response, intake_settings(settings), x_capture_id or uuid4(),
                          x_captured_at or datetime.now(timezone.utc))
    source = intake_settings(settings).media_storage_dir / str(record.id) / "capture.mp4"
    destination = video_path(record, settings)
    try:
        os.link(source, destination)
    except FileExistsError:
        if source.stat().st_ino != destination.stat().st_ino:
            if hashlib.sha256(destination.read_bytes()).hexdigest() != record.sha256:
                raise HTTPException(409, "A different file already exists at this video path.") from None
    atomic_json(destination.with_suffix(".json"), record)
    background.add_task(process_video, record.id, settings)
    return video_out(record, settings)


@router.get("/videos", response_model=list[VideoOut])
def list_videos(settings: Annotated[Settings, Depends(authorize)],
                limit: Annotated[int, Query(ge=1, le=50)] = 12) -> list[VideoOut]:
    root = intake_settings(settings).media_storage_dir
    if not root.exists():
        return []
    records = []
    for directory in root.iterdir():
        try:
            capture_id = UUID(directory.name)
        except ValueError:
            continue
        try:
            records.append(load_video(capture_id, settings))
        except HTTPException as error:
            if error.status_code != 404:
                raise
    records.sort(key=lambda r: r.received_at, reverse=True)
    return [video_out(record, settings) for record in records[:limit]]


@router.get("/videos/{capture_id}", response_model=VideoOut)
def get_video(capture_id: UUID, settings: Annotated[Settings, Depends(authorize)]) -> VideoOut:
    return video_out(load_video(capture_id, settings), settings)


@router.get("/videos/{capture_id}/content")
def get_content(capture_id: UUID, settings: Annotated[Settings, Depends(authorize)]) -> FileResponse:
    record = load_video(capture_id, settings)
    return FileResponse(video_path(record, settings), media_type="video/mp4", filename=video_filename(record))


@router.get("/videos/{capture_id}/frames/{number}")
def get_frame(capture_id: UUID, number: int, settings: Annotated[Settings, Depends(authorize)]) -> FileResponse:
    record = load_video(capture_id, settings)
    analysis = analysis_for(record, settings)
    if number < 0 or number >= analysis.total_frames:
        raise HTTPException(404, "Frame not found.")
    path = video_path(record, settings).with_suffix(".frames") / f"frame-{number + 1:03d}.jpg"
    if not path.is_file():
        raise HTTPException(404, "Frame not found.")
    return FileResponse(path, media_type="image/jpeg")


@router.post("/videos/{capture_id}/process", response_model=VideoOut, status_code=202)
def retry_video(capture_id: UUID, background: BackgroundTasks,
                settings: Annotated[Settings, Depends(authorize)]) -> VideoOut:
    record = load_video(capture_id, settings)
    background.add_task(process_video, capture_id, settings, True)
    return video_out(record, settings)


def run_tool(arguments: list[str], timeout: int) -> str:
    try:
        return subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=timeout).stdout
    except FileNotFoundError:
        raise RuntimeError(f"{arguments[0]} is not installed or is not on the backend PATH.") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{arguments[0]} timed out while reading the video.") from None
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or "Could not decode the video.").strip()[-500:]
        raise RuntimeError(f"Video decoding failed: {detail}") from None


def extract_frames(record: MediaOut, settings: Settings) -> tuple[list[Path], dict]:
    path = video_path(record, settings).resolve()
    info = json.loads(run_tool([settings.ffprobe_bin, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "format=duration:stream=width,height,codec_name", "-of", "json", str(path)], 60))
    stream = next(iter(info.get("streams", [])), None)
    if not stream:
        raise RuntimeError("The MP4 has no video track.")
    duration = float(info.get("format", {}).get("duration", "nan"))
    if not math.isfinite(duration) or not 0 < duration <= settings.video_max_seconds:
        raise RuntimeError(f"Use a clip between 0 and {settings.video_max_seconds} seconds (the demo records 15 seconds).")
    width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
    if not (0 < width <= 4096 and 0 < height <= 4096):
        raise RuntimeError("The video has unsupported frame dimensions.")
    frames_dir = path.with_suffix(".frames")
    sentinel = frames_dir / "extracted.json"
    if sentinel.exists():
        saved = json.loads(sentinel.read_text())
        cached = [frames_dir / name for name in saved.get("files", [])]
        if saved.get("sha256") == record.sha256 and cached and all(p.is_file() for p in cached):
            return cached, {"duration_seconds": duration, "width": width, "height": height, "codec": stream.get("codec_name")}
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir()
    run_tool([settings.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "2",
              "-i", str(path), "-map", "0:v:0", "-an", "-vf", "fps=1:start_time=0,scale=-2:'min(1280,ih)'",
              "-frames:v", str(settings.video_max_frames), "-q:v", "3", str(frames_dir / "frame-%03d.jpg")], 120)
    frames = sorted(frames_dir.glob("frame-*.jpg"))
    if not frames:
        # A very short Stop Early clip can round down to zero frames at 1 fps.
        run_tool([settings.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error",
                  "-i", str(path), "-map", "0:v:0", "-an", "-frames:v", "1", "-q:v", "3",
                  str(frames_dir / "frame-001.jpg")], 60)
        frames = sorted(frames_dir.glob("frame-*.jpg"))
    if not frames:
        raise RuntimeError("No usable frames could be extracted from this clip.")
    atomic_json(sentinel, {"sha256": record.sha256, "files": [p.name for p in frames]})
    return frames, {"duration_seconds": duration, "width": width, "height": height, "codec": stream.get("codec_name")}


def process_video(capture_id: UUID, settings: Settings, retry: bool = False) -> None:
    record = load_video(capture_id, settings)
    with (intake_settings(settings).media_storage_dir / str(capture_id) / ".processing.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        prior = analysis_for(record, settings)
        if prior.status == "complete" or (not retry and prior.status != "queued"):
            return
        with _worker_slot:
            analysis = prior.model_copy(deep=True)
            analysis.status = "extracting"
            analysis.model = settings.vision_model
            analysis.error = None
            analysis.updated_at = datetime.now(timezone.utc)
            client = None
            try:
                atomic_json(result_path(record, settings), analysis)
                frames, details = extract_frames(record, settings)
                for key, value in details.items():
                    setattr(analysis, key, value)
                analysis.total_frames = len(frames)
                client = create_planner_client()
                previous = {frame.number: frame for frame in prior.frames if frame.result is not None}
                analysis = VideoAnalysis(status="processing", model=settings.vision_model,
                                         total_frames=len(frames), frames=list(previous.values()), **details)
                outcomes = dict(previous)
                for number, frame_file in enumerate(frames):
                    frame = previous.get(number)
                    provider_failed = False
                    if frame is None:
                        try:
                            # Exactly the same Muse prompt, structured result validation
                            # and call logging as receiving/vision.py. Never sum counts
                            # across frames: the same carton can appear repeatedly.
                            result, latency = read_photo(client, settings.vision_model, frame_file.read_bytes(), "image/jpeg")
                            frame = FrameResult(number=number, offset_seconds=number, result=result, latency_ms=latency)
                        except Exception as error:
                            frame = FrameResult(number=number, offset_seconds=number, error=str(error)[:500])
                            # Stop after a transport/provider failure rather than sending
                            # another 14 requests to an unavailable service. Malformed
                            # individual model outputs can still yield partial results.
                            provider_failed = (isinstance(error, VisionError) and error.__cause__ is not None
                                               and not isinstance(error.__cause__, (json.JSONDecodeError, ValidationError)))
                    outcomes[number] = frame
                    analysis.frames = sorted(outcomes.values(), key=lambda item: item.number)
                    analysis.completed_frames = len(analysis.frames)
                    analysis.updated_at = datetime.now(timezone.utc)
                    atomic_json(result_path(record, settings), analysis)
                    if provider_failed:
                        break
                successes = sum(frame.result is not None for frame in analysis.frames)
                analysis.status = "complete" if successes == len(frames) else ("partial" if successes else "failed")
                if successes < len(frames):
                    analysis.error = f"{len(frames) - successes} frame(s) could not be analyzed. Retry processes only failed frames."
            except Exception as error:
                analysis.status = "failed"
                analysis.error = str(error)[:500]
                logger.warning("Video processing failed for %s (%s)", capture_id, type(error).__name__)
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
            analysis.updated_at = datetime.now(timezone.utc)
            atomic_json(result_path(record, settings), analysis)
