"""Delivery check from two glasses photos: Spark reads the slip, SAM + Spark count, code decides.

The slip answer is Muse's real read of PS-58213 (tests/fixtures/receiving/vision_live.json); SAM's
boxes and Spark's box checks are scripted per test.
"""

import io
import json
import logging
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine
from backend.models.cafe import Cafe
from backend.modules.agent import planner as planner_module
from backend.modules.inventory.models import InventoryMovement
from backend.modules.purchasing.models import PurchaseOrder
from backend.modules.receiving import service
from backend.modules.receiving.counting import Product, count_delivery, noun_for
from backend.modules.receiving.delivery_check import check_delivery
from scripts.seed_event_surge import DEMO_ORDER_ID, apply_reset, plan_reset, seed_event_surge

C = "CAFE001"
SLIP = json.loads((Path(__file__).parent / "fixtures/receiving/vision_live.json").read_text())["slip"]["parsed"]
W, H = 400, 300


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (W, H), (200, 190, 170)).save(out, format="JPEG")
    return out.getvalue()


def _boxes(n: int, conf: float = 0.95) -> str:
    """n separate boxes, 9 to a row, numbered by the code top to bottom, left to right."""
    return "".join(
        f"<|box;x1={10 + 40 * (i % 9)};y1={10 + 60 * (i // 9)};x2={40 + 40 * (i % 9)};y2={50 + 60 * (i // 9)};"
        f"w={W};h={H};c={conf}|>"
        for i in range(n)
    )


class Glasses:
    """Fake model API: SAM answers on the Responses API, Spark on Chat Completions."""

    def __init__(self, *, slip: dict, sam: dict[str, str], check: dict | None = None, point: str = "") -> None:
        self.slip, self.sam_answers, self.check_answer, self.point = slip, sam, check or {}, point
        self.sam_prompts: list[str] = []
        self.spark_prompts: list[str] = []
        self.responses = SimpleNamespace(create=self._sam)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._spark))

    def _sam(self, **kwargs):
        prompt = kwargs["input"][0]["content"][0]["text"]
        self.sam_prompts.append(prompt)
        text = self.point if prompt.startswith("<0f>") else self.sam_answers.get(prompt, "")
        return SimpleNamespace(output_text=text)

    def _spark(self, **kwargs):
        prompt = kwargs["messages"][-1]["content"][0]["text"]
        self.spark_prompts.append(prompt)
        answer = self.slip if prompt.startswith("Read this packing slip") else self.check_answer
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(answer)))], usage=None)


def _check(whole: int, oat: int, **extra) -> dict:
    boxes = [{"n": i + 1, "items": 1, "duplicate_of": None, "product": "Whole Milk" if i < whole else "Oat Milk"}
             for i in range(whole + oat)]
    return {"boxes": boxes, "missed": [], "confidence": "high", **extra}


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
        seed_event_surge(db, with_delivery=True)  # PO-1042: 2 cases whole milk + 1 case oat milk
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def _run(session: Session, client: Glasses) -> dict:
    receipt = service.start_receipt(session, C)
    return check_delivery(session, client, "muse-spark-1.3", receipt, _jpeg(), _jpeg())


def _movements(session: Session) -> list[InventoryMovement]:
    return list(session.scalars(select(InventoryMovement).where(InventoryMovement.source_type == "receipt")))


def test_everything_matches_and_is_sure_so_the_order_is_received(session: Session) -> None:
    client = Glasses(slip=SLIP, sam={"milk carton": _boxes(18)}, check=_check(12, 6))
    check = _run(session, client)

    # Look-alike cartons: one SAM call for "milk carton", Spark says which product each box is.
    assert client.sam_prompts == ["milk carton"]
    assert '"Whole Milk", "Oat Milk"' in client.spark_prompts[1]
    assert check["counts"][0]["counts"] == {"Whole Milk": 12, "Oat Milk": 6} and check["counts"][0]["confident"]

    assert (check["outcome"], check["receipt_status"], check["order_status"]) == ("received", "confirmed", "received")
    assert check["glasses_line"] == "PO-1042 received: all 2 items match. Stock updated."
    added = {m.ingredient_id: m.quantity_delta for m in _movements(session)}
    assert added == {"WHOLE_MILK": Decimal(12000), "OAT_MILK": Decimal(6000)}


def test_a_short_count_waits_for_the_owner_and_changes_nothing(session: Session) -> None:
    client = Glasses(slip=SLIP, sam={"milk carton": _boxes(17)}, check=_check(11, 6))
    check = _run(session, client)

    assert (check["outcome"], check["receipt_status"], check["order_status"]) == ("needs_owner", "draft", "confirmed")
    receipt = service.recompare(session, session.get(service.Receipt, check["receipt_id"]))
    whole = next(l for l in receipt.lines if l.ingredient_id == "WHOLE_MILK")
    assert whole.status == "short" and "1 cartons (1 L) short" in whole.message
    assert check["glasses_line"] == "PO-1042 needs your OK. whole milk: short."
    assert _movements(session) == []


def test_an_unsure_count_asks_the_owner_to_count(session: Session) -> None:
    # SAM is only 60% sure of each box, so a low count is "please count", not "short".
    client = Glasses(slip=SLIP, sam={"milk carton": _boxes(15, conf=0.6)}, check=_check(9, 6))
    check = _run(session, client)

    assert check["outcome"] == "needs_owner" and not check["counts"][0]["confident"]
    assert check["glasses_line"] == "PO-1042 needs your OK. whole milk: please count."
    assert session.get(PurchaseOrder, DEMO_ORDER_ID).status == "confirmed"


def test_an_unknown_order_is_read_and_counted_then_logged(session: Session, caplog) -> None:
    slip = {**SLIP, "customer_po_number": "PO-9999", "packing_slip_number": "PS-77001"}
    client = Glasses(slip=slip, sam={"milk carton": _boxes(18)}, check=_check(12, 6))
    with caplog.at_level(logging.WARNING, logger="cafepilot.receiving.delivery_check"):
        check = _run(session, client)

    # Counted from the slip's lines, but nothing is confirmed without an order.
    assert check["counts"][0]["counts"] == {"Whole Milk": 12, "Oat Milk": 6}
    assert (check["outcome"], check["order_found"], check["receipt_status"]) == ("order_not_found", False, "draft")
    assert check["glasses_line"] == "Order PO-9999 isn't in the system. Slip read and items counted; nothing added to stock."
    assert "order not found" in caplog.text and "PO-9999" in caplog.text
    assert _movements(session) == []


def test_spark_points_at_a_missed_item_and_sam_must_confirm_it() -> None:
    check = {"boxes": [{"n": i, "items": 1, "duplicate_of": None} for i in (1, 2, 3)],
             "missed": [{"x_pct": 90, "y_pct": 80, "note": "behind the tray"},
                        {"x_pct": 50, "y_pct": 90, "note": "same spot as box 1"}],
             "confidence": "high"}
    client = Glasses(slip=SLIP, sam={"muffin": _boxes(3)}, check=check,
                     point=f"<|box;x1=340;y1=220;x2=380;y2=260;w={W};h={H};c=0.9|>")
    photo, groups = count_delivery(client, "muse-spark-1.3", _jpeg(), "image/jpeg",
                                   [Product("Banana Nut Muffin", noun_for("BANANA_NUT_MUFFIN", "Banana Nut Muffin"))])

    # Both spots go back to SAM as points; the second answer overlaps that same box, so only one is new.
    assert [p for p in client.sam_prompts if p.startswith("<0f>")] == [
        f"<0f>1<|point;x=360;y=240;w={W};h={H}|>", f"<0f>1<|point;x=200;y=270;w={W};h={H}|>"]
    group = groups[0]
    assert (group.counts, group.missed_pointed, group.missed_confirmed) == ({"Banana Nut Muffin": 4}, 2, 1)
    assert not group.confident  # Spark found something SAM missed: not sure enough to finish on its own
    assert photo.items[0].visible_units == 4 and photo.items[0].possibly_hidden_units


def test_code_picks_what_sam_looks_for() -> None:
    assert noun_for("OAT_MILK", "Oat Milk Barista Edition, 1 L carton") == "milk carton"
    assert noun_for(None, "Plain Bagels, dozen") == "plain bagel"
    assert noun_for(None, "Glass") == "glass"
