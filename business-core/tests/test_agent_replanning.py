"""Honest statuses for orders, supplier rejection, and Muse replanning (simulated supplier)."""

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.agent import planner as planner_module
from backend.modules.agent.executor import create_run, decide, execute
from backend.modules.agent.planner import Plan
from backend.modules.agent.router import get_planner_client
from backend.modules.agent.validator import validate_plan
from backend.modules.purchasing.models import PurchaseOrder
from backend.modules.purchasing.service import settle_due
from scripts.seed_event_surge import apply_reset, plan_reset, seed_event_surge
from tests.test_agent_planner import FakeClient

C = "CAFE001"
AS_OF = "2026-10-29T18:00:00-07:00"
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


def _order_plan(supplier_id: str | None = None, delivery: str = "2026-11-01T06:00:00-08:00") -> list[dict]:
    order_args = {"cafe_id": C, "lines": "$short.order_lines", "requested_delivery_at": delivery}
    if supplier_id:
        order_args["supplier_id"] = supplier_id
    return [
        {"id": "short", "action": "calculate_shortfall", "reason": "r",
         "args": {"cafe_id": C, "as_of": AS_OF, "target_at": "2026-11-01T08:00:00-08:00",
                  "items": [{"variation_id": "COF003_REG", "quantity": 30}]}},
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
        assert order["lifecycle"][-1]["stage"] == "Confirmed" and order["external_status"] == "confirmed"
        assert order["acknowledgement"]["headline"] == "Willow Ridge Dairy Supply confirmed the order"
        assert "Arriving Sun Nov 1 by 6 AM" in order["acknowledgement"]["details"]
        assert body["waiting_on"] == []
    finally:
        app.dependency_overrides.clear()


def test_usual_supplier_rejects_early_sunday_and_muse_replans(session: Session, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(planner_module, "LLM_LOG_PATH", tmp_path / "llm.jsonl")
    run = _run(session, _order_plan())  # Bay Dairy, the usual supplier
    run = decide(session, run.run_id, "order", "approved")
    settle_due(session, cafe_id=C, now=LATER)
    order = session.get(PurchaseOrder, next(n for n in run.nodes if n.node_id == "order").result["order_id"])
    assert order.status == "rejected"
    assert order.response_reason == "Bay Dairy Co. can't deliver on Sundays before 9 AM (requested 6 AM)."

    replan = {
        "status": "plan", "goal": "Get the milk from Willow Ridge Dairy Supply",
        "assumptions": ["Willow Ridge Dairy Supply delivers Sundays from 5:30 AM"],
        "nodes": [
            {"id": "po", "action": "get_purchase_order", "reason": "r", "args": {"cafe_id": C, "order_id": order.order_id}},
            {"id": "resend", "action": "propose_supplier_order", "reason": "r",
             "args": {"cafe_id": C, "supplier_id": "WILLOW_RIDGE", "lines": "$po.reorder_lines",
                      "requested_delivery_at": "2026-11-01T06:00:00-08:00"}},
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
        sent = client.calls[0]["messages"][-1]["content"]
        assert "Bay Dairy Co. rejected purchase order" in sent and "before 9 AM" in sent
        new = response["run"]
        assert new["parent_run_id"] == run.run_id and new["parent_node_id"] == "order"
        resend = next(n for n in new["nodes"] if n["node_id"] == "resend")
        assert resend["status"] == "awaiting_approval"
        assert [l["ingredient_id"] for l in resend["resolved_args"]["lines"]] == [l.ingredient_id for l in order.lines]

        old = api.get(f"/api/v1/agent/runs/{run.run_id}").json()
        assert next(n for n in old["nodes"] if n["node_id"] == "order")["replanned_in"] == new["run_id"]
        assert api.post(f"/api/v1/agent/runs/{new['run_id']}/nodes/po/replan").status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_resending_needs_the_rejected_orders_quantities() -> None:
    plan = Plan.model_validate({"status": "plan", "goal": "g", "nodes": [
        {"id": "resend", "action": "propose_supplier_order", "reason": "r",
         "args": {"cafe_id": C, "supplier_id": "WILLOW_RIDGE",
                  "lines": [{"ingredient_id": "OAT_MILK", "quantity": "9000", "unit": "mL"}],
                  "requested_delivery_at": "2026-11-01T06:00:00-08:00"}}]})
    codes = {i.code for i in validate_plan(plan, C, timezone="America/Los_Angeles").issues}
    assert "unverified_quantity" in codes
