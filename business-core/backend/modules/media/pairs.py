"""Pair receipt and delivery photos from the local glasses receiver."""
import fcntl
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db.config import Settings, get_settings
from backend.db.session import get_db
from backend.modules.agent.router import get_planner_client
from backend.modules.media.captures import paired_photo_path
from backend.modules.media.router import authorize
from backend.modules.purchasing.models import Receipt
from backend.modules.receiving import service
from backend.modules.receiving.delivery_check import check_delivery
from backend.modules.receiving.router import _out

router = APIRouter(tags=["receiving photo pairs"])
logger = logging.getLogger("cafepilot.media.pairs")


class ValidatePairIn(BaseModel):
    delivery_id: UUID


def manifest_path(settings: Settings, delivery_id: UUID) -> Path:
    return paired_photo_path(settings, delivery_id, "receipt").parent / "manifest.json"


def read_manifest(settings: Settings, delivery_id: UUID) -> dict:
    path = manifest_path(settings, delivery_id)
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        raise HTTPException(404, "Delivery photo pair not found.") from None


def write_manifest(path: Path, manifest: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str))
    os.replace(temporary, path)


@router.post("/validate-pair")
def validate_pair(
    body: ValidatePairIn,
    settings: Annotated[Settings, Depends(authorize)],
    database: Annotated[Session, Depends(get_db)],
    client=Depends(get_planner_client),
) -> dict:
    path = manifest_path(settings, body.delivery_id)
    if not path.exists():
        raise HTTPException(404, "Delivery photo pair not found.")
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = read_manifest(settings, body.delivery_id)
        images = manifest.get("images", {})
        if not all(role in images for role in ("receipt", "delivery")):
            raise HTTPException(409, "Waiting for both receipt and delivery photos.")
        if manifest.get("status") == "complete":
            return {"status": "complete", "delivery_id": str(body.delivery_id),
                    "receipt_id": manifest.get("receipt_id"), "check": manifest.get("check"),
                    "result": manifest.get("result")}
        if manifest.get("status") == "processing":
            raise HTTPException(409, "This delivery pair is already processing.")
        manifest["status"] = "processing"
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest.pop("error", None)
        write_manifest(path, manifest)
        fcntl.flock(lock, fcntl.LOCK_UN)

    try:
        receipt_id = manifest.get("receipt_id")
        receipt = database.get(Receipt, receipt_id) if receipt_id else None
        if receipt is None:
            receipt = service.start_receipt(database, "CAFE001")
            manifest["receipt_id"] = receipt.receipt_id
            write_manifest(path, manifest)
            database.commit()
        model = settings.vision_model
        slip_bytes = paired_photo_path(settings, body.delivery_id, "receipt").read_bytes()
        photo_bytes = paired_photo_path(settings, body.delivery_id, "delivery").read_bytes()
        # Spark reads the slip, SAM + Spark count the photo, code compares and decides the outcome.
        check = check_delivery(database, client, model, receipt, slip_bytes, photo_bytes, "image/jpeg")
        database.commit()
        if check["outcome"] == "order_not_found":
            logger.warning("Delivery %s: order not found (PO on slip: %s, slip %s). Processing complete.",
                           body.delivery_id, check["po_number"] or "none", check["slip_number"] or "unread")
        else:
            logger.info("Delivery %s: %s", body.delivery_id, check["glasses_line"])
        result = _out(database, receipt).model_dump(mode="json")
        manifest.update({"status": "complete", "check": check, "result": result})
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_manifest(path, manifest)
        return {"status": "complete", "delivery_id": str(body.delivery_id),
                "receipt_id": receipt.receipt_id, "check": check, "result": result}
    except Exception as error:
        database.rollback()
        manifest["status"] = "failed"
        manifest["error"] = str(error)[:500]
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_manifest(path, manifest)
        logger.exception("Photo-pair validation failed for %s", body.delivery_id)
        if isinstance(error, HTTPException):
            raise
        raise HTTPException(502, "Could not validate this photo pair. Retry from the receiving service.") from error


@router.get("/delivery-pairs")
def list_pairs(settings: Annotated[Settings, Depends(authorize)]) -> list[dict]:
    root = Path(os.path.expanduser(str(settings.barista_shared_photos_dir)))
    if not root.exists():
        return []
    pairs = []
    for path in root.glob("*/manifest.json"):
        try:
            pairs.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(pairs, key=lambda pair: pair.get("delivery_id", ""), reverse=True)
