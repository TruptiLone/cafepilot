"""Receiving a delivery: Muse reads the slip and the photo, code compares, the owner confirms.

Muse's answers are the real ones captured from muse-spark-1.3 (tests/fixtures/receiving/vision_live.json).
"""

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.agent import planner as planner_module
from backend.modules.agent.router import get_planner_client
from backend.modules.inventory.models import InventoryMovement
from backend.modules.purchasing.models import PurchaseOrder
from backend.modules.receiving import service
from scripts.seed_event_surge import DEMO_ORDER_ID, apply_reset, plan_reset, seed_event_surge

C = "CAFE001"
LIVE = json.loads((Path(__file__).parent / "fixtures/receiving/vision_live.json").read_text())


class VisionClient:
    """Answers each call with the next captured Muse reply."""

    def __init__(self, *replies: dict) -> None:
        self.replies = [json.dumps(r) for r in replies]
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.replies.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)


@pytest.fixture
def session(tmp_path, monkeypatch) -> Session:
    monkeypatch.setattr(planner_module, "LLM_LOG_PATH", tmp_path / "llm.jsonl")
    monkeypatch.setattr(service, "IMAGE_DIR", tmp_path / "receiving")
    connection = engine.connect()
    transaction = connection.begin()
    db = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        if db.get(Cafe, C) is None:
            pytest.skip("demo data not seeded; run scripts/seed_all.sh")
        apply_reset(db, plan_reset(db))
        seed_event_surge(db)
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()
        app.dependency_overrides.clear()


def _api(session: Session, client: VisionClient) -> TestClient:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_planner_client] = lambda: client
    return TestClient(app)


def _line(body: dict, ingredient: str) -> dict:
    return next(l for l in body["lines"] if l["ingredient_id"] == ingredient)


def _checked_delivery(session: Session):
    client = VisionClient(LIVE["slip"]["parsed"], LIVE["photo"]["parsed"])
    api = _api(session, client)
    receipt = api.post("/api/v1/receiving/receipts", json={"cafe_id": C}).json()
    rid = receipt["receipt_id"]
    slip = api.post(f"/api/v1/receiving/receipts/{rid}/slip", json={"use_demo_image": True}).json()
    photo = api.post(f"/api/v1/receiving/receipts/{rid}/photo", json={"use_demo_image": True}).json()
    return api, client, rid, slip, photo


def test_open_orders_include_the_market_milk(session: Session) -> None:
    api = _api(session, VisionClient())
    orders = api.get("/api/v1/receiving/orders", params={"cafe_id": C}).json()
    po = next(o for o in orders if o["po_number"] == "PO-1042")
    assert po["supplier_name"] == "Willow Ridge Dairy Supply"
    assert [(l["supplier_item_number"], l["packs"]) for l in po["lines"]] == [("WR-1001", 2), ("WR-2240", 1)]


def test_slip_is_matched_to_the_order_and_the_photo_is_compared(session: Session) -> None:
    _, client, _, slip, photo = _checked_delivery(session)
    # Images go to Muse as data URLs, never as files or PDFs.
    image_part = client.calls[0]["messages"][-1]["content"][-1]
    assert image_part["type"] == "image_url" and image_part["image_url"]["url"].startswith("data:image/png;base64,")

    assert slip["order"]["po_number"] == "PO-1042" and slip["slip_number"] == "PS-58213"
    # The slip agrees with the order; until Muse or the owner sees the goods, each line waits for a count.
    assert [(l["supplier_item_number"], l["display"]["ordered"], l["display"]["shipped"]) for l in slip["lines"]] == [
        ("WR-1001", "12 cartons (12 L)", "12 cartons (12 L)"), ("WR-2240", "6 cartons (6 L)", "6 cartons (6 L)")]
    assert {l["status"] for l in slip["lines"]} == {"needs_count"}

    oat = _line(photo, "OAT_MILK")
    assert oat["status"] == "match" and Decimal(oat["seen_quantity"]) == Decimal(6000)
    whole = _line(photo, "WHOLE_MILK")
    assert whole["status"] == "needs_count" and whole["seen_may_hide_more"]
    assert "3 cartons" in whole["message"] and "12 cartons" in whole["message"]
    assert photo["muse_message"] == whole["message"]


def test_cannot_confirm_before_the_owner_counts(session: Session) -> None:
    api, _, rid, _, _ = _checked_delivery(session)
    response = api.post(f"/api/v1/receiving/receipts/{rid}/confirm", json={"confirmed_by": "owner"})
    assert response.status_code == 409


def test_short_delivery_adds_only_what_arrived_and_drafts_a_credit(session: Session) -> None:
    api, _, rid, _, photo = _checked_delivery(session)
    whole = _line(photo, "WHOLE_MILK")
    counted = api.post(f"/api/v1/receiving/receipts/{rid}/lines/{whole['line_no']}", json={"counted_units": 9}).json()
    whole = _line(counted, "WHOLE_MILK")
    assert whole["status"] == "short" and Decimal(whole["accepted_quantity"]) == Decimal(9000)

    done = api.post(f"/api/v1/receiving/receipts/{rid}/confirm",
                    json={"confirmed_by": "owner", "channel": "glasses"}).json()
    assert done["status"] == "confirmed"
    credit = done["credit_request"]
    assert credit["credit_ref"] == "CR-58213" and credit["status"] == "drafted"
    assert [(l["item_number"], l["display"]) for l in credit["lines"]] == [("WR-1001", "3 cartons (3 L)")]

    moves = session.scalars(select(InventoryMovement).where(
        InventoryMovement.source_type == "receipt", InventoryMovement.source_id.startswith(rid))).all()
    assert sorted((m.ingredient_id, m.quantity_delta) for m in moves) == [
        ("OAT_MILK", Decimal(6000)), ("WHOLE_MILK", Decimal(9000))]

    order = session.get(PurchaseOrder, DEMO_ORDER_ID)
    session.refresh(order)
    assert order.status == "partially_received"

    again = api.post(f"/api/v1/receiving/receipts/{rid}/confirm", json={"confirmed_by": "owner"})
    assert again.status_code == 409
    sent = api.post(f"/api/v1/receiving/receipts/{rid}/credit-request/send").json()
    assert sent["credit_request"]["status"] == "sent"
