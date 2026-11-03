from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.db.config import Settings, get_settings
from backend.media_demo import app
from backend.modules.media import captures

JPEG = b"\xff\xd8\xff\xe0paired-photo-data"


def test_pair_upload_waits_for_both_roles_then_becomes_ready(tmp_path, monkeypatch):
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
    delivery_id = uuid4()
    captured = datetime(2026, 11, 1, 18, 30, tzinfo=timezone.utc).isoformat()
    try:
        with TestClient(app) as client:
            for role in ("receipt", "delivery"):
                response = client.post("/api/upload", headers={
                    "Authorization": "Bearer test-token", "Content-Type": "image/jpeg",
                    "X-Capture-ID": str(uuid4()), "X-Captured-At": captured,
                    "X-Delivery-ID": str(delivery_id), "X-Image-Role": role,
                }, content=JPEG)
                assert response.status_code == 201
                manifest = __import__("json").loads((settings.barista_shared_photos_dir / str(delivery_id) / "manifest.json").read_text())
                assert manifest["status"] == ("waiting_for_images" if role == "receipt" else "ready")
            folder = settings.barista_shared_photos_dir / str(delivery_id)
            assert (folder / "receipt.jpg").read_bytes() == JPEG
            assert (folder / "delivery.jpg").read_bytes() == JPEG
            assert client.get("/api/receiving/delivery-pairs", headers={"Authorization": "Bearer test-token"}).json()[0]["status"] == "ready"
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_validate_pair_with_an_unknown_order_finishes_and_logs_it(tmp_path, monkeypatch, caplog):
    import json
    import logging

    from sqlalchemy.orm import sessionmaker

    from backend.db.session import engine, get_db
    from backend.modules.agent import planner as planner_module
    from backend.modules.agent.router import get_planner_client
    from backend.modules.receiving import service
    from tests.test_delivery_check import SLIP, Glasses, _boxes, _check, _jpeg

    settings = Settings(
        _env_file=None, postgres_db="unused", postgres_user="unused", postgres_password="unused",
        media_upload_token="test-token", media_storage_dir=tmp_path / "media", media_max_upload_bytes=1000,
        barista_shared_photos_dir=tmp_path / "Pictures" / "BaristaShared",
    )
    monkeypatch.setattr(planner_module, "LLM_LOG_PATH", tmp_path / "llm.jsonl")
    monkeypatch.setattr(service, "IMAGE_DIR", tmp_path / "receiving")
    delivery_id = uuid4()
    folder = settings.barista_shared_photos_dir / str(delivery_id)
    folder.mkdir(parents=True)
    for role in ("receipt", "delivery"):
        (folder / f"{role}.jpg").write_bytes(_jpeg())
    (folder / "manifest.json").write_text(json.dumps({
        "delivery_id": str(delivery_id), "status": "ready",
        "images": {"receipt": {"path": "receipt.jpg"}, "delivery": {"path": "delivery.jpg"}}}))
    glasses = Glasses(slip={**SLIP, "customer_po_number": "PO-9999"}, sam={"milk carton": _boxes(18)},
                      check=_check(12, 6))

    connection = engine.connect()
    transaction = connection.begin()
    db = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    app.dependency_overrides.update({get_settings: lambda: settings, get_db: lambda: db,
                                     get_planner_client: lambda: glasses})
    try:
        with TestClient(app) as client, caplog.at_level(logging.WARNING, logger="cafepilot.media.pairs"):
            response = client.post("/api/receiving/validate-pair", json={"delivery_id": str(delivery_id)},
                                   headers={"Authorization": "Bearer test-token"})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "complete" and body["check"]["outcome"] == "order_not_found"
        assert "order not found" in caplog.text and "PO-9999" in caplog.text
        manifest = json.loads((folder / "manifest.json").read_text())
        assert manifest["status"] == "complete" and manifest["check"]["glasses_line"].startswith("Order PO-9999")
    finally:
        app.dependency_overrides.clear()
        db.close()
        transaction.rollback()
        connection.close()
