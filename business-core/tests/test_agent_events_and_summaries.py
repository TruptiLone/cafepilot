import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.agent import planner as planner_module
from backend.modules.agent.events import detected_events, get_event
from backend.modules.agent.router import get_planner_client
from backend.modules.agent.summaries import quantity, summarize_run
from tests.test_agent_planner import FakeClient

TZ = "America/Los_Angeles"


def step(node_id: str, action: str, result=None, *, status="done", side_effect="read", args=None, **extra):
    return SimpleNamespace(node_id=node_id, action=action, status=status, side_effect=side_effect,
                           result=result, resolved_args=args, args=args or {}, error=extra.get("error"),
                           replay_of=extra.get("replay_of"))


# ---------- summaries (pure, no database) ----------------------------------------


def test_quantity_is_readable() -> None:
    assert quantity("9600.000", "mL") == "9.6 L"
    assert quantity("1080", "g") == "1.08 kg"
    assert quantity("250.000", "mL") == "250 mL"
    assert quantity("30.000", "each") == "30"


def test_every_hero_step_gets_a_plain_line() -> None:
    staff = [{"employee_id": "E3", "name": "Sofia Ramirez", "role": "barista", "active": True},
             {"employee_id": "E7", "name": "Ava Thompson", "role": "cashier", "active": True}]
    nodes = [
        step("stock", "get_stock_estimates", [
            {"ingredient_id": "OAT_MILK", "name": "Oat Milk", "base_unit": "mL", "stock_status": "known", "estimated_quantity": "3750.000"},
            {"ingredient_id": "ALMOND_MILK", "name": "Almond Milk", "base_unit": "mL", "stock_status": "unknown", "estimated_quantity": None},
        ]),
        step("short", "calculate_shortfall", {"buffer_pct": "10", "lines": [
            {"ingredient_id": "OAT_MILK", "name": "Oat Milk", "unit": "mL", "status": "short", "order_quantity": "1530"},
            {"ingredient_id": "COFFEE_BEANS", "name": "Coffee Beans", "unit": "g", "status": "enough", "order_quantity": "0"},
        ]}),
        step("coverage", "coverage_report", {"hours": [
            {"hour_start": "2026-11-01T08:00:00-08:00", "scheduled_employees": 2, "required_staff": 4, "coverage_status": "understaffed"},
            {"hour_start": "2026-11-01T09:00:00-08:00", "scheduled_employees": 2, "required_staff": 4, "coverage_status": "understaffed"},
            {"hour_start": "2026-11-01T10:00:00-08:00", "scheduled_employees": 4, "required_staff": 4, "coverage_status": "adequate"},
        ]}),
        step("staff", "list_employees", staff),
        step("add", "schedule_shift", None, status="awaiting_approval", side_effect="write",
             args={"employee_id": "E3", "start_at": "2026-11-01T07:30:00-08:00", "end_at": "2026-11-01T14:30:00-08:00"}),
        step("order", "propose_supplier_order", None, status="skipped", side_effect="write",
             error="Nothing to order: stock covers the need."),
    ]
    lines = summarize_run(nodes, "CAFE001", TZ)
    assert lines["stock"] == "Oat Milk 3.75 L, Almond Milk unknown on hand"
    assert lines["short"] == "Order 1.53 L oat milk; coffee beans enough (includes 10% buffer)"
    assert lines["coverage"] == "Short-staffed Sun Nov 1: 8 AM–10 AM has 2 of 4 people"
    assert lines["staff"] == "2 active staff members, 1 can make espresso"
    assert lines["add"] == "Schedule Sofia Ramirez, Sun Nov 1, 7:30 AM–2:30 PM"
    assert lines["order"] == "Nothing to order: stock covers the need."


def test_decided_and_replayed_writes_say_so() -> None:
    args = {"employee_id": "E3", "start_at": "2026-11-01T07:30:00-08:00", "end_at": "2026-11-01T12:00:00-08:00"}
    nodes = [
        step("staff", "list_employees", [{"employee_id": "E3", "name": "Sofia Ramirez", "role": "barista"}]),
        step("no", "schedule_shift", None, status="rejected", side_effect="write", args=args),
        step("again", "schedule_shift", {"shift_id": "S1"}, side_effect="write", args=args, replay_of="RUN-1/add"),
    ]
    lines = summarize_run(nodes, "CAFE001", TZ)
    assert lines["no"] == "Declined: Schedule Sofia Ramirez, Sun Nov 1, 7:30 AM–12 PM"
    assert lines["again"].startswith("Already done earlier, not repeated: Scheduled Sofia Ramirez")


def test_a_broken_result_never_breaks_the_run() -> None:
    nodes = [step("cov", "coverage_report", {"hours": [{"hour_start": "not a time"}]})]
    assert summarize_run(nodes, "CAFE001", TZ) == {"cov": None}


# ---------- events ----------------------------------------------------------------


def test_demo_event_is_labeled_and_uses_the_cafe_offsets() -> None:
    (event,) = detected_events("CAFE001")
    assert event.is_demo and "(demo)" in event.source
    assert event.starts_at.isoformat() == "2026-11-01T08:00:00-08:00"  # after daylight saving ends
    assert event.knowledge_as_of.isoformat() == "2026-10-29T18:00:00-07:00"
    assert [d.label for d in event.expected_demand] == ["40 extra whole-milk lattes", "20 extra oat-milk lattes"]
    assert detected_events("ANOTHER_CAFE") == []


@pytest.fixture
def session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    db = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        if db.get(Cafe, "CAFE001") is None:
            pytest.skip("demo data not seeded; run scripts/seed_all.sh")
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def test_saying_yes_to_an_event_starts_a_run(session: Session, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(planner_module, "LLM_LOG_PATH", tmp_path / "llm.jsonl")
    plan = {"status": "plan", "goal": "Check Sunday readiness", "assumptions": [],
            "nodes": [{"id": "staff", "action": "list_employees", "args": {"cafe_id": "CAFE001"}, "reason": "who works"}]}
    client = FakeClient(json.dumps(plan))
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_planner_client] = lambda: client
    try:
        api = TestClient(app)
        events = api.get("/api/v1/agent/events").json()
        event_id = events[0]["event_id"]
        assert api.get(f"/api/v1/agent/events/{event_id}").json()["title"] == "Santa Clara Farmers Market"
        response = api.post(f"/api/v1/agent/events/{event_id}/runs")
        assert api.post("/api/v1/agent/events/NOPE/runs").status_code == 404
    finally:
        app.dependency_overrides.clear()
    body = response.json()
    run = body["run"]
    assert run["query"] == get_event(event_id).request
    assert run["as_of"].startswith("2026-10-30T01:00:00")  # Thu 6 PM PDT, stored in UTC
    assert run["nodes"][0]["summary"].endswith("can make espresso")
    sent = client.calls[0]["messages"][-1]["content"]
    assert "Farmers market this Sunday Nov 1" in sent
