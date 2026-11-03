"""Updates bell: status changes read from the order, offer, run and ai_calls records."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.agent.cafe_policies import CAFE_POLICIES
from backend.modules.agent.executor import create_run, decide
from backend.modules.agent.models import AICall
from backend.modules.agent.planner import Plan
from backend.modules.agent.updates import updates
from backend.modules.agent.validator import validate_plan
from backend.modules.purchasing.service import settle_due
from scripts.seed_event_surge import apply_reset, plan_reset, seed_event_surge
from tests.test_agent_replanning import AS_OF, C, LATER, _order_plan, _run


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


def since_now() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=1)


def test_an_order_going_out_and_being_rejected(session: Session) -> None:
    start = since_now()
    run = decide(session, _run(session, _order_plan()).run_id, "order", "approved")  # Bay Dairy, oat milk included
    [sent] = updates(session, C, start)
    assert (sent["kind"], sent["group"], sent["simulated"], sent["run_id"]) == ("order_sent", "orders", True, run.run_id)
    assert sent["title"].startswith("Order PO-") and sent["title"].endswith("sent to Bay Dairy Co.")
    assert "case oat milk (6 × 1 L)" in sent["detail"]

    settle_due(session, cafe_id=C, now=LATER)
    rejected, again = updates(session, C, start)
    assert again["id"] == sent["id"]  # newest first
    assert rejected["kind"] == "order_rejected" and rejected["title"].startswith("Bay Dairy Co. declined PO-")
    assert rejected["detail"] == "Bay Dairy Co. is out of oat milk until Mon, Nov 9."


def test_shift_replies_say_who_and_the_window_in_cafe_time(session: Session, monkeypatch) -> None:
    monkeypatch.setitem(CAFE_POLICIES[C], "staff_replies", {"respond_after_seconds": 10, "declines": ["DEMO_EMP_004"]})
    window = {"start_at": "2026-11-08T08:00:00-08:00", "end_at": "2026-11-08T12:00:00-08:00"}
    run = _run(session, [
        {"id": "cov", "action": "coverage_report", "reason": "r",
         "args": {"cafe_id": C, "start_at": "2026-11-08T08:00:00-08:00", "end_at": "2026-11-08T14:00:00-08:00", "required_staff": 4}},
        {"id": "staff", "action": "list_employees", "reason": "r", "args": {"cafe_id": C}},
        {"id": "shifts", "action": "list_shifts", "reason": "r", "args": {"cafe_id": C, **window}},
        {"id": "ethan", "action": "schedule_shift", "reason": "r", "args": {"cafe_id": C, "employee_id": "DEMO_EMP_004", **window}},
        {"id": "sofia", "action": "schedule_shift", "reason": "r", "args": {"cafe_id": C, "employee_id": "DEMO_EMP_003", **window}},
    ])
    start = since_now()
    decide(session, run.run_id, "ethan", "approved")
    decide(session, run.run_id, "sofia", "approved")
    assert updates(session, C, start) == []  # offered, no reply yet
    settle_due(session, cafe_id=C, now=LATER)
    staff = {u["title"]: u for u in updates(session, C, start)}
    assert set(staff) == {"Ethan Patel declined Sun Nov 8, 8 AM–12 PM", "Sofia Ramirez confirmed Sun Nov 8, 8 AM–12 PM"}
    assert all(u["group"] == "staff" and u["simulated"] and u["run_id"] == run.run_id for u in staff.values())


def test_a_replan_started_and_finished(session: Session) -> None:
    parent = _run(session, _order_plan())
    plan = Plan.model_validate({"status": "plan", "goal": "Get the milk from Willow Ridge",
                                "nodes": [{"id": "staff", "action": "list_employees", "reason": "r", "args": {"cafe_id": C}}]})
    child = create_run(session, query="q", cafe_id=C, as_of=datetime.fromisoformat(AS_OF), plan=plan,
                       validation=validate_plan(plan, C, timezone="America/Los_Angeles"), model="muse-wonder-1.0",
                       attempts=1, parent_run_id=parent.run_id, parent_node_id="order")
    session.add(AICall(id="AIC-TEST-REPLAN", cafe_id=C, created_at=child.created_at, purpose="replan",
                       model="muse-wonder-1.0", latency_ms=5000, attempts=1, outcome="ok", run_id=child.run_id,
                       request_summary="q", error=None))
    session.flush()
    finished, started = [u for u in updates(session, C) if u["group"] == "plans"]
    assert (finished["title"], finished["detail"], finished["model"]) == ("Muse found another way", "Get the milk from Willow Ridge", "muse-wonder-1.0")
    assert started["title"] == "Muse started looking for another way"
    assert datetime.fromisoformat(finished["at"]) - datetime.fromisoformat(started["at"]) == timedelta(seconds=5)


def test_endpoint_returns_only_changes_after_since(session: Session) -> None:
    decide(session, _run(session, _order_plan("WILLOW_RIDGE")).run_id, "order", "approved")
    app.dependency_overrides[get_db] = lambda: session
    try:
        api = TestClient(app)
        everything = api.get("/api/v1/agent/updates").json()
        sent = next(u for u in everything if u["kind"] == "order_sent")
        assert api.get("/api/v1/agent/updates", params={"since": sent["at"]}).json() == [
            u for u in everything if datetime.fromisoformat(u["at"]) > datetime.fromisoformat(sent["at"])]
        assert sent["at"].endswith("-07:00") or sent["at"].endswith("-08:00")  # café time
        assert api.get("/api/v1/agent/updates", params={"since": "2026-11-08T08:00:00"}).status_code == 422
    finally:
        app.dependency_overrides.clear()
