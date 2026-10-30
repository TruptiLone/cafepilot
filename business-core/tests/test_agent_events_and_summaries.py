import json
from decimal import Decimal
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

MARKET = "EVT_SC_FARMERS_MARKET_20261101"


@pytest.fixture
def session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    db = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        if db.get(Cafe, "CAFE001") is None or get_event(db, MARKET) is None:
            pytest.skip("demo history not seeded; run scripts/seed_sales_history.py")
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def test_demo_event_comes_from_the_calendar_with_history_numbers(session: Session) -> None:
    events = {e.event_id: e for e in detected_events(session, "CAFE001")}
    market = events[MARKET]
    assert market.is_demo and "(demo)" in market.source
    assert market.starts_at.isoformat().startswith("2026-11-01T16:00:00")  # 8 AM PST, after DST ends
    assert market.knowledge_as_of.isoformat().startswith("2026-10-30T01:00:00")  # Thu 6 PM PDT
    expected = market.expected
    assert expected.confidence == "good" and expected.examples == 6
    assert expected.extra_drinks_low <= expected.extra_drinks_typical <= expected.extra_drinks_high
    assert f"typically +{expected.extra_drinks_typical}" in market.muse_message
    assert MARKET in market.request and "estimate_event_demand" in market.request
    assert events["EVT_HALLOWEEN_20261031"].expected.confidence == "rough"
    assert detected_events(session, "ANOTHER_CAFE") == []


def test_saying_yes_to_an_event_starts_a_run(session: Session, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(planner_module, "LLM_LOG_PATH", tmp_path / "llm.jsonl")
    plan = {"status": "plan", "goal": "Check Sunday readiness", "assumptions": [],
            "nodes": [{"id": "staff", "action": "list_employees", "args": {"cafe_id": "CAFE001"}, "reason": "who works"}]}
    client = FakeClient(json.dumps(plan))
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_planner_client] = lambda: client
    try:
        api = TestClient(app)
        assert MARKET in {e["event_id"] for e in api.get("/api/v1/agent/events").json()}
        assert api.get(f"/api/v1/agent/events/{MARKET}").json()["title"] == "Santa Clara Farmers Market"
        response = api.post(f"/api/v1/agent/events/{MARKET}/runs")
        assert api.post("/api/v1/agent/events/NOPE/runs").status_code == 404
    finally:
        app.dependency_overrides.clear()
    run = response.json()["run"]
    assert run["query"] == get_event(session, MARKET).request
    assert run["as_of"].startswith("2026-10-30T01:00:00")
    assert run["nodes"][0]["summary"].endswith("can make espresso")
    sent = client.calls[0]["messages"][-1]["content"]
    assert MARKET in sent and "upcoming_events" in sent


def test_demand_and_packed_order_lines_read_naturally() -> None:
    demand = step("demand", "estimate_event_demand", {
        "confidence": "good", "drinks": {"normal": 96, "extra_low": 19, "extra_typical": 35, "extra_high": 53},
        "food": {"extra_typical": 7}, "examples": [{"name": "Farmers Market"}] * 6})
    short = step("short", "calculate_shortfall", {"buffer_pct": "10", "include_normal_use": True, "lines": [
        {"ingredient_id": "OAT_MILK", "name": "Oat Milk", "unit": "mL", "status": "short", "order_quantity": "5000",
         "packs": 5, "pack_name": "1 L carton"},
        *[{"ingredient_id": f"I{i}", "name": f"Item {i}", "unit": "each", "status": "enough", "order_quantity": "0"}
          for i in range(3)]]})
    lines = summarize_run([demand, short], "CAFE001", TZ)
    assert lines["demand"] == "Last 6 Farmers Markets: +19 to +53 (typically +35) drinks on a normal 96; pastries +7"
    assert lines["short"] == ("Order 5 L oat milk (5 × 1 L carton); everything else is covered "
                              "(after normal use and regular deliveries; includes 10% buffer)")


def test_cafe_orders_are_rounded_up_to_whole_packs(session: Session) -> None:
    from backend.modules.agent.registry import REGISTRY

    result = REGISTRY["calculate_shortfall"].run(session, {
        "cafe_id": "CAFE001", "as_of": "2026-10-29T18:00:00-07:00", "target_at": "2026-11-01T08:00:00-08:00",
        "items": [{"variation_id": "COF003_REG", "quantity": 30}], "buffer_pct": "0"})
    whole = next(l for l in result["lines"] if l["ingredient_id"] == "WHOLE_MILK")
    assert whole["status"] == "short"  # 30 x 240 mL = 7.2 L against 3 L counted on Thursday
    assert Decimal(whole["shortfall"]) == Decimal("4200")
    assert whole["order_quantity"] == "5000" and whole["packs"] == 5 and whole["pack_name"] == "1 L carton"
