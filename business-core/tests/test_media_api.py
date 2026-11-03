"""The upload contract is independent of PostgreSQL and of model API calls."""
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.db.config import Settings, get_settings
from backend.media_demo import app  # the glasses receiver app serves the media routes

JPEG = b"\xff\xd8\xff\xe0" + b"test-jpeg" * 8
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"test-video" * 8


@pytest.fixture
def service(tmp_path):
    settings = Settings(_env_file=None, postgres_db="unused", postgres_user="unused", postgres_password="unused",
                        media_upload_token="test-token", media_storage_dir=tmp_path / "media",
                        media_max_upload_bytes=1000)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, settings
    app.dependency_overrides.pop(get_settings, None)


def headers(mime="image/jpeg", capture_id=None):
    return {"Authorization": "Bearer test-token", "Content-Type": mime,
            "X-Capture-ID": str(capture_id or uuid4()), "X-Captured-At": "2026-10-30T20:00:00-07:00"}


@pytest.mark.parametrize("mime,body,kind", [("image/jpeg", JPEG, "photo"), ("video/mp4", MP4, "video")])
def test_upload_round_trip_and_retry_are_idempotent(service, mime, body, kind):
    client, settings = service
    h = headers(mime)
    first = client.post("/api/v1/media", headers=h, content=body)
    assert first.status_code == 201
    record = first.json()
    assert record["kind"] == kind and record["size_bytes"] == len(body)
    assert record["id"] == h["X-Capture-ID"]
    retry = client.post("/api/v1/media", headers=h, content=body)
    assert retry.status_code == 200 and retry.json() == record
    auth = {"Authorization": "Bearer test-token"}
    assert client.get(record["content_url"], headers=auth).content == body
    assert client.get(f"/api/v1/media/{record['id']}", headers=auth).json() == record
    assert len(list(settings.media_storage_dir.iterdir())) == 1


def test_same_capture_id_cannot_overwrite_different_bytes(service):
    client, _ = service
    h = headers()
    first = client.post("/api/v1/media", headers=h, content=JPEG).json()
    assert client.post("/api/v1/media", headers=h, content=JPEG + b"changed").status_code == 409
    assert client.get(first["content_url"], headers=h).content == JPEG


def test_credentials_required_for_uploads_and_reads(service):
    client, settings = service
    assert client.post("/api/v1/media", headers={k: v for k, v in headers().items() if k != "Authorization"}, content=JPEG).status_code == 401
    assert client.get("/api/v1/media/status").status_code == 401
    assert client.get(f"/api/v1/media/{uuid4()}/content").status_code == 401
    settings.media_upload_token = None
    assert client.get("/api/v1/media/status", headers=headers()).status_code == 503


@pytest.mark.parametrize("mime,body,expected", [("text/plain", JPEG, 415), ("image/jpeg", b"", 400),
                                             ("image/jpeg", b"not-a-photo", 415), ("video/mp4", JPEG, 415),
                                             ("image/jpeg", JPEG * 30, 413)])
def test_bad_media_is_rejected_without_publishing_files(service, mime, body, expected):
    client, settings = service
    assert client.post("/api/v1/media", headers=headers(mime), content=body).status_code == expected
    assert not settings.media_storage_dir.exists() or not list(settings.media_storage_dir.iterdir())


def test_chunked_body_limit_and_cleanup(service):
    client, settings = service
    def chunks():
        yield JPEG
        yield b"x" * 1200
    assert client.post("/api/v1/media", headers=headers(), content=chunks()).status_code == 413
    assert not list(settings.media_storage_dir.iterdir())


def test_untrusted_ids_and_naive_capture_dates(service):
    client, _ = service
    h = headers()
    h["X-Capture-ID"] = "../../outside"
    assert client.post("/api/v1/media", headers=h, content=JPEG).status_code == 422
    h = headers()
    h["X-Captured-At"] = "2026-10-30T20:00:00"
    assert client.post("/api/v1/media", headers=h, content=JPEG).status_code == 422


def test_status_exposes_contract_without_secrets(service):
    client, _ = service
    response = client.get("/api/v1/media/status", headers=headers())
    assert response.json()["service"] == "cafepilot-media"
    assert response.json()["max_upload_bytes"] == 1000
    assert "test-token" not in response.text
