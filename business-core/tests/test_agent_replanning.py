"""Honest statuses for orders, supplier rejection, and Muse replanning (simulated supplier)."""

import json
import re
from datetime import datetime, timedelta, timezone

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


def test_usual_supplier_is_out_of_oat_milk_and_muse_replans(session: Session, tmp_path, monkeypatch, ai_call_rows) -> None:
    monkeypatch.setattr(planner_module, "LLM_LOG_PATH", tmp_path / "llm.jsonl")
    run = _run(session, _order_plan())  # Bay Dairy, the usual supplier
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
    assert [o["name"] for o in found["suppliers"]] == ["Bay Dairy Co.", "Willow Ridge Dairy Supply"]
    assert all(o["carries_all"] and o["can_deliver_in_time"] for o in found["suppliers"])
    assert found["recommended_supplier_id"] == "BAY_DAIRY"  # usual supplier; its stock-out shows only when it answers
    order = next(n for n in run.nodes if n.node_id == "order")
    assert order.status == "awaiting_approval" and order.resolved_args["supplier_id"] == "BAY_DAIRY"
