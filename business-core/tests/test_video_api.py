"""Real MP4 decoding with a fake model transport; never calls a model or database."""
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.db.config import Settings, get_settings
from backend.media_demo import app  # the glasses receiver app serves the media routes
from backend.modules.media import videos
from backend.modules.receiving import vision

READ = {"items": [{"product": "Whole milk", "visible_units": 2, "possibly_hidden_units": True,
                   "confidence": "high"}], "notes": "The same two cartons are visible."}


class Model:
    def __init__(self):
        self.calls = []
        self.responses = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        value = self.responses.pop(0) if self.responses else json.dumps(READ)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=value))])

    def close(self):
        pass


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("Video integration tests require the existing ffmpeg/ffprobe executables.")
    path = tmp_path_factory.mktemp("video-sample") / "sample.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=white:s=48x64:r=15", "-t", "2", "-an", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(path)], check=True, timeout=120)
    return path.read_bytes()


@pytest.fixture
def service(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, postgres_db="unused", postgres_user="unused", postgres_password="unused",
                        media_upload_token="test-token", video_storage_dir=tmp_path / "uploads/videos")
    model = Model()
    monkeypatch.setattr(videos, "create_planner_client", lambda: model)
    monkeypatch.setattr(vision, "_log_call", lambda _: None)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, settings, model
    app.dependency_overrides.pop(get_settings, None)


def headers():
    return {"Authorization": "Bearer test-token", "Content-Type": "video/mp4", "X-Capture-ID": str(uuid4()),
            "X-Captured-At": "2026-10-31T07:00:00Z", "X-Capture-Source": "test"}


def test_video_upload_processing_playback_and_retry(service, sample):
    client, settings, model = service
    h = headers()
    uploaded = client.post("/api/upload-video", headers=h, content=sample)
    assert uploaded.status_code == 201
    record = uploaded.json()
    saved = settings.video_storage_dir / record["filename"]
    assert saved.read_bytes() == sample
    assert saved.with_suffix(".json").exists() and saved.with_suffix(".result.json").exists()
    current = client.get(f"/api/videos/{record['id']}", headers=h).json()
    processing = current["processing"]
    assert processing["status"] == "complete"
    assert processing["duration_seconds"] == 2 and (processing["width"], processing["height"]) == (48, 64)
    assert processing["total_frames"] == 2 and len(model.calls) == 2
    # Same counts in two views are retained as separate observations, never added.
    assert [frame["result"]["items"][0]["visible_units"] for frame in processing["frames"]] == [2, 2]
    assert all(call["messages"][0]["content"][0]["text"] == vision.PHOTO_PROMPT for call in model.calls)
    assert client.get(record["url"], headers=h).content == sample
    partial = client.get(record["url"], headers={**h, "Range": "bytes=0-15"})
    assert partial.status_code == 206 and partial.content == sample[:16]
    frame = client.get(f"/api/videos/{record['id']}/frames/0", headers=h)
    assert frame.content.startswith(b"\xff\xd8\xff")
    repeated = client.post("/api/upload-video", headers=h, content=sample)
    assert repeated.status_code == 200 and repeated.json()["id"] == record["id"]
    assert len(model.calls) == 2
    assert len(list(settings.video_storage_dir.glob("*.mp4"))) == 1


def test_partial_analysis_retries_only_failed_frames(service, sample):
    client, _, model = service
    model.responses = ["not valid JSON", json.dumps(READ)]
    h = headers()
    record = client.post("/api/upload-video", headers=h, content=sample).json()
    current = client.get(f"/api/videos/{record['id']}", headers=h).json()
    assert current["processing"]["status"] == "partial" and len(model.calls) == 2
    assert client.post(f"/api/videos/{record['id']}/process", headers=h).status_code == 202
    current = client.get(f"/api/videos/{record['id']}", headers=h).json()
    assert current["processing"]["status"] == "complete" and len(model.calls) == 3


def test_provider_outage_stops_calls_and_keeps_video(service, sample):
    client, settings, model = service
    model.responses = [RuntimeError("network unavailable")]
    h = headers()
    record = client.post("/api/upload-video", headers=h, content=sample).json()
    current = client.get(f"/api/videos/{record['id']}", headers=h).json()
    assert current["processing"]["status"] == "failed" and len(model.calls) == 1
    assert (settings.video_storage_dir / record["filename"]).read_bytes() == sample
    assert client.post(f"/api/videos/{record['id']}/process", headers=h).status_code == 202
    assert client.get(f"/api/videos/{record['id']}", headers=h).json()["processing"]["status"] == "complete"


def test_video_list_latest_first_and_authentication(service, sample):
    client, _, _ = service
    for _ in range(2):
        assert client.post("/api/upload-video", headers=headers(), content=sample).status_code == 201
    listing = client.get("/api/videos?limit=1", headers=headers()).json()
    assert len(listing) == 1 and listing[0]["source"] == "test"
    assert client.get("/api/videos").status_code == 401
    assert client.get(listing[0]["url"]).status_code == 401
    assert client.post("/api/upload-video", headers={"Content-Type": "video/mp4"}, content=sample).status_code == 401
    assert client.get("/api/videos?limit=1000", headers=headers()).status_code == 422


def test_id_collision_and_invalid_media_preserve_original(service, sample):
    client, _, _ = service
    h = headers()
    record = client.post("/api/upload-video", headers=h, content=sample).json()
    assert client.post("/api/upload-video", headers=h, content=sample + b"changed").status_code == 409
    assert client.get(record["url"], headers=h).content == sample
    assert client.post("/api/upload-video", headers={**headers(), "Content-Type": "image/jpeg"}, content=sample).status_code == 415
    assert client.post("/api/upload-video", headers=headers(), content=b"not a video").status_code == 415
    assert client.get(f"/api/videos/{record['id']}/frames/-1", headers=h).status_code == 404
    assert client.get(f"/api/videos/{uuid4()}", headers=h).status_code == 404


def test_oversized_upload_not_published(service, sample):
    client, settings, _ = service
    settings.media_max_upload_bytes = 10
    assert client.post("/api/upload-video", headers=headers(), content=sample).status_code == 413
    assert not settings.video_storage_dir.exists()


def test_undecodable_mp4_reports_failure_and_retains_file(service):
    client, settings, model = service
    body = b"\x00\x00\x00\x18ftypisom" + b"broken"
    h = headers()
    record = client.post("/api/upload-video", headers=h, content=body).json()
    current = client.get(f"/api/videos/{record['id']}", headers=h).json()
    assert current["processing"]["status"] == "failed"
    assert "decoding failed" in current["processing"]["error"]
    assert (settings.video_storage_dir / record["filename"]).read_bytes() == body
    assert not model.calls
