"""Adapter to the existing receiving photo reader. No inventory or receipt writes."""
from datetime import datetime, timezone
import fcntl
import json
import logging
import os
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel

from backend.db.config import Settings
from backend.modules.agent.planner import create_planner_client
from backend.modules.media.router import read_metadata
from backend.modules.receiving.vision import PhotoRead, read_photo

logger = logging.getLogger("cafepilot.media")


class Analysis(BaseModel):
    status: Literal["queued", "processing", "complete", "failed"] = "queued"
    model: str | None = None
    latency_ms: int | None = None
    result: PhotoRead | None = None
    error: str | None = None
    updated_at: datetime | None = None


def load_analysis(directory: Path) -> Analysis:
    try:
        return Analysis.model_validate_json((directory / "analysis.json").read_text())
    except FileNotFoundError:
        return Analysis()


def save_analysis(directory: Path, analysis: Analysis) -> None:
    temporary = directory / f".analysis-{uuid4()}.tmp"
    try:
        temporary.write_text(analysis.model_dump_json(indent=2))
        os.replace(temporary, directory / "analysis.json")
    finally:
        temporary.unlink(missing_ok=True)


def process_photo(capture_id: UUID, settings: Settings, retry: bool = False) -> None:
    directory = settings.media_storage_dir / str(capture_id)
    # Separate workers and retries share a file lock, including across processes.
    with (directory / ".analysis.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        prior = load_analysis(directory)
        if not retry and prior.status != "queued":
            return
        record = read_metadata(directory)
        if record.kind != "photo":
            return
        save_analysis(directory, Analysis(status="processing", model=settings.vision_model,
                                         updated_at=datetime.now(timezone.utc)))
        client = None
        try:
            client = create_planner_client()
            # Use the exact same Muse reader, prompt, validation and call logging
            # as the existing receiving feature. Do not invent a second pipeline.
            result, latency = read_photo(client, settings.vision_model,
                                         (directory / "capture.jpg").read_bytes(), record.content_type)
            analysis = Analysis(status="complete", model=settings.vision_model,
                                latency_ms=latency, result=result, updated_at=datetime.now(timezone.utc))
        except Exception as error:
            logger.warning("Photo analysis failed for %s (%s)", capture_id, type(error).__name__)
            analysis = Analysis(status="failed", model=settings.vision_model,
                                error=str(error)[:500], updated_at=datetime.now(timezone.utc))
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
        save_analysis(directory, analysis)
