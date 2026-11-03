from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.db.config import Settings, get_settings
from backend.media_demo import app
from backend.modules.media import captures


def test_photo_upload_copies_idempotently_to_mac_shared_folder(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        postgres_db="unused",
        postgres_user="unused",
        postgres_password="unused",
        media_upload_token="test-token",
        media_storage_dir=tmp_path / "media",
        media_max_upload_bytes=1000,
        barista_shared_photos_dir=tmp_path / "Pictures" / "BaristaShared",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr(captures, "process_photo", lambda *_args, **_kwargs: None)
    capture_id = uuid4()
    captured = datetime(2026, 11, 1, 18, 30, tzinfo=timezone.utc)
    headers = {
        "Authorization": "Bearer test-token",
        "Content-Type": "image/jpeg",
        "X-Capture-ID": str(capture_id),
        "X-Captured-At": captured.isoformat(),
    }
    body = b"\xff\xd8\xff\xe0local-photo"
    try:
        with TestClient(app) as client:
            first = client.post("/api/upload", headers=headers, content=body)
            assert first.status_code == 201
            destination = settings.barista_shared_photos_dir / f"20261101T183000Z_{capture_id}.jpg"
            assert destination.read_bytes() == body
            retry = client.post("/api/upload", headers=headers, content=body)
            assert retry.status_code == 200
            assert len(list(settings.barista_shared_photos_dir.glob("*.jpg"))) == 1
    finally:
        app.dependency_overrides.pop(get_settings, None)
