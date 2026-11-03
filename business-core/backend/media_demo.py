"""Authenticated media intake and local glasses receiving pair coordinator.

Run from business-core: uvicorn backend.media_demo:app --host 127.0.0.1 --port 8003
The normal web app uses backend.main; both share the same file store and reader.
"""
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI
from backend.db.config import get_settings

from backend.modules.media.captures import router as captures_router
from backend.modules.media.router import router as media_router
from backend.modules.media.videos import router as videos_router
from backend.modules.media.pairs import router as pairs_router

logger = logging.getLogger("cafepilot.media.monitor")


def recover_stale_pairs() -> None:
    settings = get_settings()
    root = Path(os.path.expanduser(str(settings.barista_shared_photos_dir)))
    for path in root.glob("*/manifest.json"):
        try:
            manifest = json.loads(path.read_text())
            changed = datetime.fromisoformat(manifest.get("updated_at", "1970-01-01T00:00:00+00:00"))
            if manifest.get("status") == "processing" and datetime.now(timezone.utc) - changed > timedelta(minutes=10):
                manifest["status"] = "ready"
                manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
                temp = path.with_suffix(".monitor-tmp")
                temp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
                os.replace(temp, path)
        except (OSError, ValueError, json.JSONDecodeError):
            logger.warning("Could not recover delivery-pair manifest %s", path)


async def monitor_pairs() -> None:
    settings = get_settings()
    token = settings.media_upload_token.get_secret_value().strip() if settings.media_upload_token else ""
    port = int(os.environ.get("BARISTA_PHOTO_PORT", "8004"))
    while True:
        try:
            root = Path(os.path.expanduser(str(settings.barista_shared_photos_dir)))
            for manifest_path in root.glob("*/manifest.json"):
                try:
                    manifest = json.loads(manifest_path.read_text())
                    if manifest.get("status") != "ready":
                        continue
                    request = Request(
                        f"http://127.0.0.1:{port}/api/receiving/validate-pair",
                        data=json.dumps({"delivery_id": manifest["delivery_id"]}).encode(),
                        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                        method="POST",
                    )
                    # A SAM + Spark count can take a couple of minutes on a crowded photo.
                    await asyncio.to_thread(urlopen, request, timeout=300)
                except (OSError, HTTPError, URLError, KeyError, json.JSONDecodeError) as error:
                    logger.warning("Could not trigger pair validation (%s)", type(error).__name__)
        except Exception:
            logger.exception("Local delivery-pair monitor failed")
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_app):
    # BARISTA_PAIR_MONITOR=0 serves the routes without watching the shared folder (tests).
    if os.environ.get("BARISTA_PAIR_MONITOR", "1") == "0":
        yield
        return
    recover_stale_pairs()
    task = asyncio.create_task(monitor_pairs())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

app = FastAPI(title="Cafepilot glasses bridge", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.include_router(captures_router, prefix="/api")
app.include_router(media_router, prefix="/api/v1/media")
app.include_router(videos_router, prefix="/api")
app.include_router(pairs_router, prefix="/api/receiving")
