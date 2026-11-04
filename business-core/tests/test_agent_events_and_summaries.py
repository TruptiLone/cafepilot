import json
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.config import get_settings
from backend.db.session import engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.agent import planner as planner_module
from backend.modules.agent.events import detected_events, get_event
from backend.modules.agent.models import AICall
from backend.modules.agent.router import get_planner_client
from backend.modules.agent.summaries import quantity, summarize_run
from backend.modules.agent.events import event_staffing
from backend.modules.agent.planner import SYSTEM_PROMPT
from scripts.seed_event_surge import apply_reset, plan_reset, seed_event_surge
from tests.test_agent_planner import FakeClient

TZ = "America/Los_Angeles"
AS_OF_THU = "2026-11-05T18:00:00-08:00"


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
            {"hour_start": "2026-11-08T08:00:00-08:00", "scheduled_employees": 2, "required_staff": 4, "coverage_status": "understaffed"},
            {"hour_start": "2026-11-08T09:00:00-08:00", "scheduled_employees": 2, "required_staff": 4, "coverage_status": "understaffed"},
            {"hour_start": "2026-11-08T10:00:00-08:00", "scheduled_employees": 4, "required_staff": 4, "coverage_status": "adequate"},
        ]}),
        step("staff", "list_employees", staff),
        step("add", "schedule_shift", None, status="awaiting_approval", side_effect="write",
             args={"employee_id": "E3", "start_at": "2026-11-08T07:30:00-08:00", "end_at": "2026-11-08T14:30:00-08:00"}),
        step("order", "propose_supplier_order", None, status="skipped", side_effect="write",
             error="Nothing to order: stock covers the need."),
    ]
    lines = summarize_run(nodes, "CAFE001", TZ)
    assert lines["stock"] == "Oat Milk 3.75 L, Almond Milk unknown on hand"
    assert lines["short"] == "Order 1.53 L oat milk; coffee beans enough (includes 10% buffer)"
    assert lines["coverage"] == "Short-staffed Sun Nov 8: 8 AM–10 AM has 2 of 4 people"
    assert lines["staff"] == "2 active staff members, 1 can make espresso"
    assert lines["add"] == "Schedule Sofia Ramirez, Sun Nov 8, 7:30 AM–2:30 PM"
    assert lines["order"] == "Nothing to order: stock covers the need."


def test_decided_and_replayed_writes_say_so() -> None:
    args = {"employee_id": "E3", "start_at": "2026-11-08T07:30:00-08:00", "end_at": "2026-11-08T12:00:00-08:00"}
    nodes = [
        step("staff", "list_employees", [{"employee_id": "E3", "name": "Sofia Ramirez", "role": "barista"}]),
        step("no", "schedule_shift", None, status="rejected", side_effect="write", args=args),
        step("again", "schedule_shift", {"shift_id": "S1"}, side_effect="write", args=args, replay_of="RUN-1/add"),
    ]
    lines = summarize_run(nodes, "CAFE001", TZ)
    assert lines["no"] == "Declined: Schedule Sofia Ramirez, Sun Nov 8, 7:30 AM–12 PM"
    assert lines["again"].startswith("Already done earlier, not repeated: Scheduled Sofia Ramirez")


def test_a_broken_result_never_breaks_the_run() -> None:
    nodes = [step("cov", "coverage_report", {"hours": [{"hour_start": "not a time"}]})]
    assert summarize_run(nodes, "CAFE001", TZ) == {"cov": None}


# ---------- events ----------------------------------------------------------------

MARKET = "EVT_SC_FARMERS_MARKET_20261108"


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
    assert market.starts_at.isoformat().startswith("2026-11-08T16:00:00")  # 8 AM PST, after DST ends
    assert market.knowledge_as_of.isoformat().startswith("2026-11-06T02:00:00")  # Thu 6 PM PST
    expected = market.expected
    assert expected.confidence == "good" and expected.examples == 6
    assert expected.extra_drinks_low <= expected.extra_drinks_typical <= expected.extra_drinks_high
    assert f"typically +{expected.extra_drinks_typical}" in market.muse_message
    assert MARKET in market.request and "estimate_event_demand" in market.request
    # In café time, like knowledge_as_of in the planning context (a UTC time here failed rule 7 every run).
    assert "knowledge as of 2026-11-05T18:00:00-08:00" in market.request
    assert events["EVT_THANKSGIVING_20261126"].expected.confidence == "rough"  # one past edition
    assert "fewer drinks" in events["EVT_THANKSGIVING_20261126"].muse_message
    assert set(events) == {MARKET, "EVT_THANKSGIVING_20261126", "EVT_HOLIDAY_FAIR_20261205"}  # no Halloween, no Nov 1
    assert detected_events(session, "ANOTHER_CAFE") == []


def test_events_name_the_configured_planner_model(session: Session, monkeypatch) -> None:
    """So the UI can say "Planning · <model>" before the plan comes back."""
    monkeypatch.setattr(get_settings(), "planner_model", "muse-test-9.9")
    app.dependency_overrides[get_db] = lambda: session
    try:
        api = TestClient(app)
        listed = {e["event_id"]: e for e in api.get("/api/v1/agent/events").json()}
        one = api.get(f"/api/v1/agent/events/{MARKET}").json()
    finally:
        app.dependency_overrides.clear()
    assert listed[MARKET]["planner_model"] == "muse-test-9.9"
    assert one["planner_model"] == "muse-test-9.9"
    assert {e["planner_model"] for e in listed.values()} == {"muse-test-9.9"}


@pytest.fixture
def market_day(session: Session) -> Session:
    """The demo Sunday as seeded: 2 people 8 AM-12 PM, 3 people 12-2 PM, before any plan."""
    apply_reset(session, plan_reset(session))
    seed_event_surge(session)
    return session


def test_planner_sees_upcoming_events_in_cafe_time(session: Session) -> None:
    from backend.modules.agent.events import upcoming_for_planner
    from backend.modules.events.models import CafeEvent

    listed = {e["event_id"]: e for e in upcoming_for_planner(session, "CAFE001", datetime.fromisoformat(AS_OF_THU))}
    market = listed[MARKET]
    assert (market["starts_at"], market["ends_at"]) == ("2026-11-08T08:00:00-08:00", "2026-11-08T14:00:00-08:00")
    stored = session.get(CafeEvent, MARKET)
    assert datetime.fromisoformat(market["starts_at"]) == stored.starts_at  # same moment
    assert listed["EVT_THANKSGIVING_20261126"]["starts_at"] == "2026-11-26T07:00:00-08:00"


def test_local_time_reads_utc_z_as_the_same_moment() -> None:
    from zoneinfo import ZoneInfo

    from backend.modules.agent.summaries import local_time

    moment = local_time("2026-11-08T16:00:00Z", ZoneInfo(TZ))
    assert moment.isoformat() == "2026-11-08T08:00:00-08:00"
    assert moment == datetime.fromisoformat("2026-11-08T16:00:00+00:00")
    assert local_time("not a time", ZoneInfo(TZ)) is None


def test_event_staffing_shows_short_windows_and_roster(market_day: Session) -> None:
    staffing = event_staffing(market_day, "CAFE001", MARKET)
    assert staffing["required_staff"] == 4
    assert staffing["required_from"] == "cafe_policies.staffing.busy_event_required_staff"
    windows = [(w["start_at"][11:16], w["end_at"][11:16], w["scheduled"], w["required"], w["short_by"])
               for w in staffing["coverage_by_window"]]
    assert windows == [("08:00", "12:00", 2, 4, 2), ("12:00", "14:00", 3, 4, 1)]
    assert staffing["short_windows"] == staffing["coverage_by_window"]
    on_shift = {r["employee_id"] for r in staffing["roster"]}
    assert len(on_shift) == len(staffing["roster"]) > 0
    assert all(r["start_at"].startswith("2026-11-08") and r["role"] for r in staffing["roster"])


def test_event_staffing_follows_the_schedule_and_policy(market_day: Session) -> None:
    from backend.modules.staffing.schemas import ShiftCreate
    from backend.modules.staffing.service import StaffingService

    roster = event_staffing(market_day, "CAFE001", MARKET)["roster"]
    free = next(e for e in ("DEMO_EMP_003", "DEMO_EMP_004") if e not in {r["employee_id"] for r in roster})
    StaffingService(market_day).schedule_shift(ShiftCreate(
        cafe_id="CAFE001", employee_id=free,
        start_at=datetime.fromisoformat("2026-11-08T08:00:00-08:00"), end_at=datetime.fromisoformat("2026-11-08T12:00:00-08:00")))
    staffing = event_staffing(market_day, "CAFE001", MARKET)
    assert [(w["scheduled"], w["short_by"]) for w in staffing["coverage_by_window"]] == [(3, 1)]
    assert free in {r["employee_id"] for r in staffing["roster"]}
    assert event_staffing(market_day, "CAFE001", "EVT_THANKSGIVING_20261126")["required_from"].endswith("normal_required_staff")
    assert event_staffing(market_day, "ANOTHER_CAFE", MARKET) is None


def test_event_plans_get_the_staffing_typed_requests_do_not(market_day: Session, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(planner_module, "LLM_LOG_PATH", tmp_path / "llm.jsonl")
    plan = {"status": "plan", "goal": "g", "assumptions": [],
            "nodes": [{"id": "staff", "action": "list_employees", "args": {"cafe_id": "CAFE001"}, "reason": "who"}]}
    client = FakeClient(json.dumps(plan))
    app.dependency_overrides[get_db] = lambda: market_day
    app.dependency_overrides[get_planner_client] = lambda: client
    try:
        api = TestClient(app)
        api.post(f"/api/v1/agent/events/{MARKET}/runs")
        api.post("/api/v1/agent/runs", json={"query": "who works Sunday?", "cafe_id": "CAFE001"})
    finally:
        app.dependency_overrides.clear()
    sent = [json.loads(call["messages"][-1]["content"])["reference_data"] for call in client.calls]
    assert [(w["short_by"]) for w in sent[0]["event_staffing"]["short_windows"]] == [2, 1]
    assert "event_staffing" not in sent[1]
    assert "9b." in SYSTEM_PROMPT and "9c." in SYSTEM_PROMPT and "event_staffing" in SYSTEM_PROMPT


def test_saying_yes_to_an_event_starts_a_run(session: Session, tmp_path, monkeypatch, ai_call_rows) -> None:
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
    assert run["as_of"].startswith("2026-11-06T02:00:00")
    assert run["nodes"][0]["summary"].endswith("can make espresso")
    sent = client.calls[0]["messages"][-1]["content"]
    assert MARKET in sent and "upcoming_events" in sent
    with ai_call_rows() as rows:  # AI activity names the event, not its ID
        call = rows.scalar(select(AICall).where(AICall.run_id == run["run_id"]))
    assert call.request_summary == "Plan: Santa Clara Farmers Market"


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
        "cafe_id": "CAFE001", "as_of": "2026-11-05T18:00:00-08:00", "target_at": "2026-11-08T08:00:00-08:00",
        "items": [{"variation_id": "COF003_REG", "quantity": 30}], "buffer_pct": "0"})
    whole = next(l for l in result["lines"] if l["ingredient_id"] == "WHOLE_MILK")
    assert whole["status"] == "short"  # 30 x 240 mL = 7.2 L against 0.5 L counted on Thursday
    assert Decimal(whole["shortfall"]) == Decimal("6700")
    assert whole["order_quantity"] == "7560" and whole["packs"] == 4 and whole["pack_name"] == "half gallon (1.89 L)"


def test_approved_changes_are_acknowledged_with_real_coverage(session: Session) -> None:
    from datetime import datetime as dt

    from backend.modules.agent.executor import create_run, decide, execute
    from backend.modules.agent.planner import Plan
    from backend.modules.agent.validator import validate_plan
    from backend.modules.agent.acknowledgements import change_statuses
    from backend.modules.purchasing.service import settle_due
    from scripts.seed_event_surge import apply_reset, plan_reset, seed_event_surge

    apply_reset(session, plan_reset(session))  # clean scenario inside this rolled-back test
    seed_event_surge(session)
    window = {"start_at": "2026-11-08T08:00:00-08:00", "end_at": "2026-11-08T14:00:00-08:00"}
    nodes = [
        {"id": "cov", "action": "coverage_report", "reason": "r", "args": {"cafe_id": "CAFE001", **window, "required_staff": 4}},
        {"id": "staff", "action": "list_employees", "reason": "r", "args": {"cafe_id": "CAFE001"}},
        {"id": "shifts", "action": "list_shifts", "reason": "r", "args": {"cafe_id": "CAFE001", **window}},
        {"id": "add", "action": "schedule_shift", "reason": "r", "args": {
            "cafe_id": "CAFE001", "employee_id": "DEMO_EMP_003",
            "start_at": "2026-11-08T07:30:00-08:00", "end_at": "2026-11-08T14:30:00-08:00"}},
    ]
    plan = Plan.model_validate({"status": "plan", "goal": "g", "nodes": nodes})
    run = create_run(session, query="q", cafe_id="CAFE001", as_of=dt.fromisoformat("2026-11-05T18:00:00-08:00"),
                     plan=plan, validation=validate_plan(plan, "CAFE001"), model="m", attempts=1)
    run = execute(session, run.run_id)
    statuses, closing, waiting = change_statuses(session, run)
    assert closing is None and [st.stage for st in statuses["add"].lifecycle] == ["Proposed"]

    before = next(n for n in run.nodes if n.node_id == "cov").result["hours"][0]["scheduled_employees"]
    run = decide(session, run.run_id, "add", "approved")
    statuses, closing, waiting = change_statuses(session, run)
    status = statuses["add"]
    assert [st.stage for st in status.lifecycle] == ["Proposed", "Approved", "Offered to Sofia", "Waiting for Sofia"]
    assert status.external_status == "offered" and waiting == ["Sofia"]
    assert status.acknowledgement.headline == "Shift offered to Sofia Ramirez"
    assert "once offers are accepted" in status.acknowledgement.details[1]
    assert f"{before + 1} of 4" in status.acknowledgement.details[1]  # re-measured from the schedule

    settle_due(session, now=dt.fromisoformat("2030-01-01T00:00:00+00:00"))  # Sofia's simulated reply
    statuses, closing, waiting = change_statuses(session, run)
    status = statuses["add"]
    assert status.lifecycle[-1].stage == "Accepted" and status.external_status == "accepted"
    assert status.acknowledgement.headline == "Sofia Ramirez accepted: Sofia will be in"
    assert "staffing now" in status.acknowledgement.details[1] and waiting == []
    assert closing.startswith("Here's where things stand: Sofia Ramirez accepted")
