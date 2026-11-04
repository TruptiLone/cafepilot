"""Honest statuses for orders, supplier rejection, and Muse replanning (simulated supplier)."""

import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.agent import planner as planner_module
from backend.modules.agent.executor import create_run, decide, execute
from backend.modules.agent.models import AICall
from backend.modules.agent.planner import Plan
from backend.modules.agent.router import get_planner_client
from backend.modules.agent.validator import validate_plan
from backend.modules.purchasing.models import PurchaseOrder
from backend.modules.purchasing import service as purchasing_service
from backend.modules.purchasing.service import DemoTiming, settle_due
from backend.modules.receiving import service as receiving_service
from scripts.seed_event_surge import apply_reset, plan_reset, seed_event_surge
from tests.test_agent_planner import FakeClient

C = "CAFE001"
AS_OF = "2026-11-05T18:00:00-08:00"
LATER = datetime(2030, 1, 1, tzinfo=timezone.utc)  # past every simulated reply


@pytest.fixture
def session() -> Session:
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


def _order_plan(supplier_id: str | None = None, delivery: str = "2026-11-08T06:00:00-08:00") -> list[dict]:
    order_args = {"cafe_id": C, "lines": "$short.order_lines", "requested_delivery_at": delivery}
    if supplier_id:
        order_args["supplier_id"] = supplier_id
    return [
        {"id": "short", "action": "calculate_shortfall", "reason": "r",
         "args": {"cafe_id": C, "as_of": AS_OF, "target_at": "2026-11-08T08:00:00-08:00",
                  "items": [{"variation_id": "COF003_REG", "quantity": 30},
                            {"variation_id": "COF003_REG", "quantity": 20,
                             "modifiers": [{"modifier_id": "MILK_OAT", "quantity": 1}]}]}},
        {"id": "order", "action": "propose_supplier_order", "reason": "r", "args": order_args},
    ]


def _run(session: Session, nodes: list[dict]):
    plan = Plan.model_validate({"status": "plan", "goal": "g", "nodes": nodes})
    validation = validate_plan(plan, C, timezone="America/Los_Angeles")
    assert validation.valid, validation.issues
    run = create_run(session, query="q", cafe_id=C, as_of=datetime.fromisoformat(AS_OF), plan=plan,
                     validation=validation, model="m", attempts=1)
    return execute(session, run.run_id)


def _api(session: Session, client=None) -> TestClient:
    app.dependency_overrides[get_db] = lambda: session
    if client is not None:
        app.dependency_overrides[get_planner_client] = lambda: client
    return TestClient(app)


def test_approved_order_is_sent_then_answered_honestly(session: Session) -> None:
    run = _run(session, _order_plan("WILLOW_RIDGE"))
    run = decide(session, run.run_id, "order", "approved")
    try:
        api = _api(session)
        body = api.get(f"/api/v1/agent/runs/{run.run_id}").json()
        order = next(n for n in body["nodes"] if n["node_id"] == "order")
        assert [s["stage"] for s in order["lifecycle"]] == [
            "Proposed", "Approved", "Sent to Willow Ridge Dairy Supply", "Waiting for confirmation"]
        assert order["external_status"] == "sent" and body["waiting_on"] == ["Willow Ridge Dairy Supply"]
        assert order["acknowledgement"]["headline"] == "Order sent to Willow Ridge Dairy Supply (simulated)"
        assert any(d.startswith("Total $") for d in order["acknowledgement"]["details"])
        assert "Waiting on Willow Ridge Dairy Supply" in body["closing_message"]

        settle_due(session, cafe_id=C, now=LATER)
        body = api.get(f"/api/v1/agent/runs/{run.run_id}").json()
        order = next(n for n in body["nodes"] if n["node_id"] == "order")
        # Confirmed is still in progress: the delivery hasn't been checked in.
        assert [s["stage"] for s in order["lifecycle"]][-2:] == ["Confirmed", "Awaiting delivery"]
        assert order["lifecycle"][-1]["state"] == "current" and order["external_status"] == "confirmed"
        assert order["acknowledgement"]["headline"] == "Willow Ridge Dairy Supply confirmed – awaiting delivery"
        assert order["acknowledgement"]["tone"] == "awaiting"
        assert "Arriving Sun Nov 8 by 6 AM" in order["acknowledgement"]["details"]
        assert body["waiting_on"] == []

        # The delivery check confirms a receipt against the order: Delivered.
        po = session.get(PurchaseOrder, order["result"]["order_id"])
        receipt = receiving_service.start_receipt(session, C, po.order_id)
        receipt.slip_number, receipt.status, receipt.confirmed_at = "PS-58214", "confirmed", LATER
        po.status = "received"
        session.flush()
        body = api.get(f"/api/v1/agent/runs/{run.run_id}").json()
        order = next(n for n in body["nodes"] if n["node_id"] == "order")
        assert order["lifecycle"][-1]["stage"] == "Delivered" and order["lifecycle"][-1]["state"] == "done"
        assert order["acknowledgement"]["headline"] == "Willow Ridge Dairy Supply delivered the order"
        assert order["acknowledgement"]["tone"] == "done"
        assert "Checked in, slip PS-58214: everything matched" in order["acknowledgement"]["details"]
        updates = api.get("/api/v1/agent/updates", params={"cafe_id": C}).json()
        assert any(u["kind"] == "order_delivered" and u["title"].startswith(f"{po.po_number} delivered") for u in updates)

        po.status, po.lines[0].received_quantity = "partially_received", 0
        session.flush()
        order = next(n for n in api.get(f"/api/v1/agent/runs/{run.run_id}").json()["nodes"] if n["node_id"] == "order")
        assert order["lifecycle"][-1] == {**order["lifecycle"][-1], "stage": "Partly delivered", "state": "failed"}
        assert order["acknowledgement"]["tone"] == "problem"
        assert any(d.startswith("Still missing") for d in order["acknowledgement"]["details"])
    finally:
        app.dependency_overrides.clear()


def _reply_wait(session: Session) -> timedelta:
    run = decide(session, _run(session, _order_plan("WILLOW_RIDGE")).run_id, "order", "approved")
    order = session.get(PurchaseOrder, next(n for n in run.nodes if n.node_id == "order").result["order_id"])
    return order.respond_at - order.sent_at


def test_suppliers_reply_after_their_usual_time_by_default(session: Session, monkeypatch) -> None:
    monkeypatch.setattr(purchasing_service, "demo_timing", lambda: DemoTiming(demo_supplier_reply_delay_seconds=0))
    assert _reply_wait(session) == timedelta(seconds=8)
    assert DemoTiming(_env_file=None).demo_supplier_reply_delay_seconds == 0


def test_demo_delay_holds_the_supplier_reply_back(session: Session, monkeypatch) -> None:
    monkeypatch.setattr(purchasing_service, "demo_timing", lambda: DemoTiming(demo_supplier_reply_delay_seconds=30))
    assert _reply_wait(session) == timedelta(seconds=38)
    monkeypatch.setenv("DEMO_SUPPLIER_REPLY_DELAY_SECONDS", "12")
    assert DemoTiming(_env_file=None).demo_supplier_reply_delay_seconds == 12


def test_supplier_out_of_oat_milk_rejects_and_muse_replans(session: Session, tmp_path, monkeypatch, ai_call_rows) -> None:
    monkeypatch.setattr(planner_module, "LLM_LOG_PATH", tmp_path / "llm.jsonl")
    run = _run(session, _order_plan("BAY_DAIRY"))
    run = decide(session, run.run_id, "order", "approved")
    settle_due(session, cafe_id=C, now=LATER)
    order = session.get(PurchaseOrder, next(n for n in run.nodes if n.node_id == "order").result["order_id"])
    assert order.status == "rejected"
    assert order.response_reason == "Bay Dairy Co. is out of oat milk until Mon, Nov 9."

    replan = {
        "status": "plan", "goal": "Get the milk from Willow Ridge Dairy Supply",
        "assumptions": ["Willow Ridge Dairy Supply delivers Sundays from 5:30 AM"],
        "nodes": [
            {"id": "po", "action": "get_purchase_order", "reason": "r", "args": {"cafe_id": C, "order_id": order.order_id}},
            {"id": "resend", "action": "propose_supplier_order", "reason": "r",
             "args": {"cafe_id": C, "supplier_id": "WILLOW_RIDGE", "lines": "$po.reorder_lines",
                      "requested_delivery_at": "2026-11-08T06:00:00-08:00"}},
        ],
    }
    client = FakeClient(json.dumps(replan))
    try:
        api = _api(session, client)
        old = api.get(f"/api/v1/agent/runs/{run.run_id}").json()
        failed = next(n for n in old["nodes"] if n["node_id"] == "order")
        assert failed["lifecycle"][-1]["stage"] == "Rejected by supplier"
        assert failed["acknowledgement"]["next_step"] == "find_alternative"
        assert "Something needs another plan" in old["closing_message"]

        response = api.post(f"/api/v1/agent/runs/{run.run_id}/nodes/order/replan").json()
        logged = [json.loads(line) for line in (tmp_path / "llm.jsonl").read_text().splitlines()]
        assert [(r["purpose"], r["attempt"]) for r in logged] == [("replan", 1)]
        sent = client.calls[0]["messages"][-1]["content"]
        assert "Bay Dairy Co. rejected purchase order" in sent and "out of oat milk" in sent
        new = response["run"]
        assert new["parent_run_id"] == run.run_id and new["parent_node_id"] == "order"
        resend = next(n for n in new["nodes"] if n["node_id"] == "resend")
        assert resend["status"] == "awaiting_approval"
        assert [l["ingredient_id"] for l in resend["resolved_args"]["lines"]] == [l.ingredient_id for l in order.lines]

        old = api.get(f"/api/v1/agent/runs/{run.run_id}").json()
        assert next(n for n in old["nodes"] if n["node_id"] == "order")["replanned_in"] == new["run_id"]
        with ai_call_rows() as rows:  # AI activity says what happened, in plain words
            call = rows.scalar(select(AICall).where(AICall.run_id == new["run_id"]))
        assert (call.purpose, call.request_summary) == ("replan", "Replan: Bay Dairy Co. declined, find another milk supplier")
        assert api.post(f"/api/v1/agent/runs/{new['run_id']}/nodes/po/replan").status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_declined_shift_replan_states_the_shift_in_cafe_time(session: Session, monkeypatch) -> None:
    from backend.modules.agent.cafe_policies import CAFE_POLICIES
    from backend.modules.agent.replanning import replan_request, replan_summary

    monkeypatch.setitem(CAFE_POLICIES["CAFE001"], "staff_replies", {"respond_after_seconds": 10, "declines": ["DEMO_EMP_004"]})
    window = {"start_at": "2026-11-08T08:00:00-08:00", "end_at": "2026-11-08T12:00:00-08:00"}
    run = _run(session, [
        {"id": "cov", "action": "coverage_report", "reason": "r",
         "args": {"cafe_id": C, "start_at": "2026-11-08T08:00:00-08:00", "end_at": "2026-11-08T14:00:00-08:00", "required_staff": 4}},
        {"id": "staff", "action": "list_employees", "reason": "r", "args": {"cafe_id": C}},
        {"id": "shifts", "action": "list_shifts", "reason": "r", "args": {"cafe_id": C, **window}},
        {"id": "add", "action": "schedule_shift", "reason": "r", "args": {"cafe_id": C, "employee_id": "DEMO_EMP_004", **window}},
    ])
    run = decide(session, run.run_id, "add", "approved")
    assert next(n for n in run.nodes if n.node_id == "add").result["start_at"].endswith("Z")  # results are UTC
    settle_due(session, cafe_id=C, now=LATER)

    text = replan_request(session, run, "add")
    assert "declined the shift Sun Nov 8, 8 AM–12 PM (2026-11-08T08:00:00-08:00 to 2026-11-08T12:00:00-08:00)" in text
    assert not re.search(r"T\d\d:\d\d(:\d\d)?(Z|\+00:00)", text)  # no UTC timestamps left
    assert replan_summary(session, run, "add") == "Replan: Ethan Patel declined Sun Nov 8, 8 AM–12 PM, find someone else"


def test_resending_needs_the_rejected_orders_quantities() -> None:
    plan = Plan.model_validate({"status": "plan", "goal": "g", "nodes": [
        {"id": "resend", "action": "propose_supplier_order", "reason": "r",
         "args": {"cafe_id": C, "supplier_id": "WILLOW_RIDGE",
                  "lines": [{"ingredient_id": "OAT_MILK", "quantity": "9000", "unit": "mL"}],
                  "requested_delivery_at": "2026-11-08T06:00:00-08:00"}}]})
    codes = {i.code for i in validate_plan(plan, C, timezone="America/Los_Angeles").issues}
    assert "unverified_quantity" in codes


def test_find_suppliers_prices_the_order_and_code_picks_the_supplier(session: Session) -> None:
    nodes = _order_plan()
    nodes.insert(1, {"id": "suppliers", "action": "find_suppliers", "reason": "r",
                     "args": {"cafe_id": C, "lines": "$short.order_lines", "deliver_by": "2026-11-08T06:00:00-08:00"}})
    nodes[2]["args"]["supplier_id"] = "$suppliers.recommended_supplier_id"
    run = _run(session, nodes)
    found = next(n for n in run.nodes if n.node_id == "suppliers").result
    assert [o["name"] for o in found["suppliers"]] == ["Willow Ridge Dairy Supply", "Bay Dairy Co."]
    assert all(o["carries_all"] and o["can_deliver_in_time"] for o in found["suppliers"])
    assert found["recommended_supplier_id"] == "WILLOW_RIDGE"  # the usual supplier
    order = next(n for n in run.nodes if n.node_id == "order")
    assert order.status == "awaiting_approval" and order.resolved_args["supplier_id"] == "WILLOW_RIDGE"


def test_a_short_delivery_is_reordered_from_another_supplier(session: Session, monkeypatch) -> None:
    from backend.modules.agent.acknowledgements import change_statuses
    from backend.modules.agent.registry import REGISTRY
    from backend.modules.agent.replanning import replan_request, replan_summary

    run = _run(session, _order_plan())  # Willow Ridge, the usual supplier: 4 half gallons of whole milk
    run = decide(session, run.run_id, "order", "approved")
    settle_due(session, cafe_id=C, now=LATER)
    po = session.get(PurchaseOrder, next(n for n in run.nodes if n.node_id == "order").result["order_id"])
    milk = next(l for l in po.lines if l.ingredient_id == "WHOLE_MILK")
    assert (po.status, po.supplier_id, milk.packs, milk.pack_name) == ("confirmed", "WILLOW_RIDGE", 4, "half gallon (1.89 L)")

    # The glasses counted 3 of the 4 cartons and the owner confirmed: 1 half gallon never arrived.
    receipt = receiving_service.start_receipt(session, C, po.order_id)
    receipt.slip_number, receipt.status, receipt.confirmed_at = "PS-58216", "confirmed", LATER
    for line in po.lines:
        line.received_quantity = line.quantity - (milk.pack_size if line is milk else 0)
    po.status = "partially_received"
    session.flush()

    order = change_statuses(session, run)[0]["order"].acknowledgement
    assert order.next_step == "find_alternative"  # the plan replans it right away
    assert "Still missing 1.89 L whole milk" in order.details
    text = replan_request(session, run, "order")
    assert "Willow Ridge Dairy Supply delivered purchase order" in text and "(packing slip PS-58216)" in text
    assert "1.89 L whole milk never arrived" in text and "another supplier than Willow Ridge Dairy Supply" in text
    assert replan_summary(session, run, "order") == "Replan: Willow Ridge Dairy Supply delivered short, find another milk supplier"
    # Only what is missing is re-sent: code decides the quantity, not the model.
    again = REGISTRY["get_purchase_order"].run(session, {"cafe_id": C, "order_id": po.order_id})
    assert [(l["ingredient_id"], l["quantity"]) for l in again["reorder_lines"]] == [("WHOLE_MILK", "1890.000")]


def test_the_market_packing_slip_with_one_carton_missing_is_short_by_one_half_gallon(session: Session) -> None:
    """PS-58216 as printed (pack column "4 cans × ½ gal", "10 croissants"): sizes come from the order, not the slip."""
    from backend.modules.agent.registry import REGISTRY
    from backend.modules.purchasing.service import OrderLineIn, send_order
    from backend.modules.receiving.vision import PhotoItem, PhotoRead, SlipLine, SlipRead

    po = send_order(session, cafe_id=C, supplier_id="WILLOW_RIDGE", order_id="PO_TEST_MARKET",
                    requested_delivery_at=datetime.fromisoformat("2026-11-08T06:00:00-08:00"), lines=[
                        OrderLineIn("WHOLE_MILK", Decimal("6190"), "mL"), OrderLineIn("COFFEE_BEANS", Decimal("163.8"), "g"),
                        OrderLineIn("BUTTER_CROISSANT", Decimal("10"), "each")])
    settle_due(session, cafe_id=C, now=LATER)
    assert (po.po_number, po.status, [l.packs for l in po.lines]) == ("PO-123", "confirmed", [4, 1, 10])

    receipt = receiving_service.start_receipt(session, C)
    receipt = receiving_service.attach_slip(session, receipt, SlipRead(
        supplier="Willow Ridge Dairy Supply", packing_slip_number="PS-58216", customer_po_number="PO-123", lines=[
            SlipLine(item_number="WR-1175", description="Whole Milk, half gallon (1.89 L)", pack="4 cans × ½ gal", ordered=4, shipped=4, backordered=0),
            SlipLine(item_number="WR-2210", description="Coffee Beans, 12 oz bag", pack="1 bag × 12 oz", ordered=1, shipped=1, backordered=0),
            SlipLine(item_number="WR-3305", description="Butter Croissants, baked", pack="10 croissants", ordered=10, shipped=10, backordered=0)]), "m")
    receipt = receiving_service.attach_photo(session, receipt, PhotoRead(items=[
        PhotoItem(product="Whole Milk", visible_units=3), PhotoItem(product="Espresso Coffee Beans", visible_units=1),
        PhotoItem(product="Butter Croissant", visible_units=10)]), "m")
    assert receipt.order_id == po.order_id
    assert [(l.ingredient_id, l.status) for l in receipt.lines] == [
        ("WHOLE_MILK", "short"), ("COFFEE_BEANS", "match"), ("BUTTER_CROISSANT", "match")]
    assert receipt.lines[0].message == "Muse can see 3 cartons (5.67 L), but the slip says 4 cartons (7.56 L): 1 carton (1.89 L) short."

    receiving_service.confirm(session, receipt)
    session.refresh(po)
    assert po.status == "partially_received"
    again = REGISTRY["get_purchase_order"].run(session, {"cafe_id": C, "order_id": po.order_id})
    assert [(l["ingredient_id"], l["quantity"]) for l in again["reorder_lines"]] == [("WHOLE_MILK", "1890.000")]


def test_a_misread_item_number_still_matches_the_order_line_by_its_description(session: Session) -> None:
    """From a real glasses photo: Muse read WR-1175 as WR-1270 and WR-2210 as WB-2210."""
    from backend.modules.purchasing.service import OrderLineIn, send_order
    from backend.modules.receiving.counting import noun_for
    from backend.modules.receiving.vision import SlipLine, SlipRead

    send_order(session, cafe_id=C, supplier_id="WILLOW_RIDGE", order_id="PO_TEST_MARKET",
               requested_delivery_at=datetime.fromisoformat("2026-11-08T06:00:00-08:00"), lines=[
                   OrderLineIn("WHOLE_MILK", Decimal("6190"), "mL"), OrderLineIn("COFFEE_BEANS", Decimal("163.8"), "g")])
    receipt = receiving_service.attach_slip(session, receiving_service.start_receipt(session, C), SlipRead(
        packing_slip_number="PS-58216", customer_po_number="PO-123", lines=[
            SlipLine(item_number="WR-1270", description="Whole Milk, half gallon (1.89 L)", pack="4 cans - 1/2 gal", shipped=4),
            SlipLine(item_number="WB-2210", description="Coffee Beans, 12 oz bag", pack="1 bag = 12 oz", shipped=1)]), "m")
    assert [(l.ingredient_id, l.order_line_no is not None) for l in receipt.lines] == [("WHOLE_MILK", True), ("COFFEE_BEANS", True)]
    assert noun_for("WHOLE_MILK", "Whole Milk, half gallon (1.89 L)") == "plastic jug"  # SAM finds jugs, not cartons
    assert noun_for("WHOLE_MILK", "Whole Milk, 1 L carton") == "milk carton"


def test_an_unsure_count_above_the_slip_asks_the_owner_instead_of_calling_it_over(session: Session) -> None:
    """From a real glasses photo: 10 croissants piled in a bowl, SAM found 10, Muse pointed at one more."""
    from backend.modules.purchasing.service import OrderLineIn, send_order
    from backend.modules.receiving.vision import PhotoItem, PhotoRead, SlipLine, SlipRead

    send_order(session, cafe_id=C, supplier_id="WILLOW_RIDGE", order_id="PO_TEST_MARKET",
               requested_delivery_at=datetime.fromisoformat("2026-11-08T06:00:00-08:00"),
               lines=[OrderLineIn("BUTTER_CROISSANT", Decimal("10"), "each")])
    receipt = receiving_service.attach_slip(session, receiving_service.start_receipt(session, C), SlipRead(
        packing_slip_number="PS-58216", customer_po_number="PO-123",
        lines=[SlipLine(item_number="WR-3305", description="Butter Croissants, baked", pack="10 croissants", shipped=10)]), "m")
    unsure = PhotoRead(items=[PhotoItem(product="Butter Croissant", visible_units=11, possibly_hidden_units=True)])
    [line] = receiving_service.attach_photo(session, receipt, unsure, "m").lines
    assert (line.status, line.message) == ("needs_count", "Muse counted 11 units (11 each) of butter croissants but isn't sure; "
                                                          "the slip says 10 units (10 each). Please count it.")
    sure = PhotoRead(items=[PhotoItem(product="Butter Croissant", visible_units=11)])
    [line] = receiving_service.attach_photo(session, receipt, sure, "m").lines
    assert line.status == "over"  # a sure count above the slip is still "over"


def test_a_fixed_link_per_po_opens_its_delivery_check(session: Session) -> None:
    """/receiving?po=PO-123 for the demo: the checked-in delivery, else the newest glasses check."""
    from backend.modules.purchasing.service import OrderLineIn, send_order
    from backend.modules.receiving.vision import SlipLine, SlipRead

    send_order(session, cafe_id=C, supplier_id="WILLOW_RIDGE", order_id="PO_TEST_MARKET",
               requested_delivery_at=datetime.fromisoformat("2026-11-08T06:00:00-08:00"),
               lines=[OrderLineIn("BUTTER_CROISSANT", Decimal("10"), "each")])
    settle_due(session, cafe_id=C, now=LATER)
    try:
        api = _api(session)
        url = "/api/v1/receiving/orders/PO-123/check"
        assert api.get(url).json()["detail"] == "No delivery check for PO-123 yet."
        assert api.get("/api/v1/receiving/orders/PO-999/check").status_code == 404
        receiving_service.start_receipt(session, C, "PO_TEST_MARKET")  # started by hand, nothing read: not shown

        slip = SlipRead(packing_slip_number="PS-58216", customer_po_number="PO-123",
                        lines=[SlipLine(item_number="WR-3305", description="Butter Croissants, baked", shipped=10)])
        checks = []
        for minutes in (1, 2):
            receipt = receiving_service.start_receipt(session, C)
            receipt.started_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
            checks.append(receiving_service.attach_slip(session, receipt, slip, "m").receipt_id)
        session.flush()
        assert api.get(url).json()["receipt_id"] == checks[1]  # the newest check
        assert api.get("/api/v1/receiving/orders/po-123/check").json()["receipt_id"] == checks[1]

        first = session.get(receiving_service.Receipt, checks[0])
        first.status = "confirmed"
        session.flush()
        assert api.get(url).json()["receipt_id"] == checks[0]  # once checked in, that one stays on the link
    finally:
        app.dependency_overrides.clear()



def test_received_lists_checked_in_deliveries_newest_first(session: Session) -> None:
    from backend.modules.purchasing.service import OrderLineIn, send_order

    orders = [send_order(session, cafe_id=C, supplier_id=supplier, order_id=f"PO_TEST_{supplier}",
                         requested_delivery_at=datetime.fromisoformat("2026-11-08T06:00:00-08:00"),
                         lines=[OrderLineIn("WHOLE_MILK", Decimal("1890"), "mL")]) for supplier in ("WILLOW_RIDGE", "BAY_DAIRY")]
    for minutes, (order, status, slip) in enumerate(zip(orders, ("partially_received", "received"), ("PS-58216", "PS-58217"))):
        receipt = receiving_service.start_receipt(session, C, order.order_id)
        receipt.status, receipt.slip_number = "confirmed", slip
        receipt.confirmed_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        order.status = status
    receiving_service.start_receipt(session, C, orders[0].order_id)  # a draft: not received yet
    session.flush()
    try:
        api = _api(session)
        assert api.get("/api/v1/receiving/orders").json() == []  # neither is still expected
        rows = api.get("/api/v1/receiving/received").json()
        assert [(r["po_number"], r["supplier_name"], r["order_status"], r["slip_number"]) for r in rows] == [
            ("PO-124", "Bay Dairy Co.", "received", "PS-58217"),
            ("PO-123", "Willow Ridge Dairy Supply", "partially_received", "PS-58216")]
    finally:
        app.dependency_overrides.clear()
