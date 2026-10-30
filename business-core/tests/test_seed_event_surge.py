from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine
from backend.modules.agent.executor import create_run, decide, execute
from backend.modules.agent.planner import Plan, build_planning_context
from backend.modules.agent.registry import REGISTRY
from backend.modules.agent.validator import validate_plan
from backend.modules.inventory.models import InventoryMovement, StockCount
from backend.modules.staffing.models import Employee, Shift
from scripts.seed_demo_operations import DemoSeedError
from scripts.seed_event_surge import (
    AS_OF,
    PREFIX,
    apply_reset,
    plan_reset,
    seed_event_surge,
)

pytestmark = pytest.mark.database


@pytest.fixture
def session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    db = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        if db.get(Employee, "DEMO_EMP_002") is None:
            pytest.skip("demo data not seeded; run scripts/seed_all.sh")
        apply_reset(db, plan_reset(db))  # start from a clean scenario, rolled back afterwards
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def _january_counts(db: Session) -> tuple[int, int]:
    counts = db.scalar(select(func.count()).select_from(StockCount).where(StockCount.count_id.startswith("DEMO_")))
    moves = db.scalar(
        select(func.count()).select_from(InventoryMovement).where(InventoryMovement.movement_id.startswith("DEMO_"))
    )
    return counts, moves


def test_seed_is_idempotent(session: Session) -> None:
    first = seed_event_surge(session)
    assert (first.counts, first.count_lines, first.movements, first.shifts) == (1, 7, 1, 3)
    again = seed_event_surge(session)
    assert (again.counts, again.count_lines, again.movements, again.shifts) == (0, 0, 0, 0)


def test_scenario_tells_the_event_story(session: Session) -> None:
    seed_event_surge(session)
    stock = {
        row["ingredient_id"]: row["estimated_quantity"]
        for row in REGISTRY["get_stock_estimates"].run(session, {"cafe_id": "CAFE001", "as_of": AS_OF})
    }
    assert stock["WHOLE_MILK"] == "3000.000"  # low at close, before the next morning's delivery
    assert stock["OAT_MILK"] == "1250.000"  # 1,500 counted, 250 spilled after the count
    coverage = REGISTRY["coverage_report"].run(
        session,
        {
            "cafe_id": "CAFE001",
            "start_at": "2026-11-01T08:00:00-08:00",
            "end_at": "2026-11-01T14:00:00-08:00",
            "required_staff": 4,
        },
    )
    assert [hour["scheduled_employees"] for hour in coverage["hours"]] == [2, 2, 2, 2, 3, 3]


def test_reset_removes_agent_changes_and_scenario_but_not_january(session: Session) -> None:
    january_before = _january_counts(session)
    seed_event_surge(session)
    plan = Plan.model_validate(
        {
            "status": "plan",
            "goal": "Add a barista",
            "nodes": [
                {"id": "cov", "action": "coverage_report", "reason": "r", "args": {"start_at": "2026-11-01T08:00:00-08:00", "end_at": "2026-11-01T14:00:00-08:00", "required_staff": 4}},
                {"id": "staff", "action": "list_employees", "reason": "r", "args": {}},
                {"id": "shifts", "action": "list_shifts", "reason": "r", "args": {"start_at": "2026-11-01T07:00:00-08:00", "end_at": "2026-11-01T15:00:00-08:00"}},
                {"id": "add", "action": "schedule_shift", "reason": "r", "args": {"employee_id": "DEMO_EMP_003", "start_at": "2026-11-01T07:30:00-08:00", "end_at": "2026-11-01T14:30:00-08:00"}},
            ],
        }
    )
    run = create_run(
        session, query="q", cafe_id="CAFE001", as_of=datetime(2026, 10, 30, 1, tzinfo=timezone.utc),
        plan=plan, validation=validate_plan(plan, "CAFE001"), model="m", attempts=1,
    )
    execute(session, run.run_id)
    run = decide(session, run.run_id, "add", "approved")
    agent_shift = next(n for n in run.nodes if n.node_id == "add").result["shift_id"]

    reset = plan_reset(session)
    assert run.run_id in reset.runs
    assert agent_shift in reset.shifts
    assert {f"{PREFIX}SHIFT_002", f"{PREFIX}SHIFT_005", f"{PREFIX}SHIFT_007"} <= set(reset.shifts)
    assert reset.counts == [f"{PREFIX}COUNT_THU_CLOSE"]

    apply_reset(session, reset)
    assert plan_reset(session).empty
    assert session.get(Shift, agent_shift) is None
    assert _january_counts(session) == january_before


def test_conflicting_existing_row_is_not_overwritten(session: Session) -> None:
    seed_event_surge(session)
    shift = session.get(Shift, f"{PREFIX}SHIFT_007")
    shift.end_at = datetime.fromisoformat("2026-11-01T13:00:00-08:00")
    session.flush()
    with pytest.raises(DemoSeedError, match="Conflicting"):
        seed_event_surge(session)


def test_planner_context_carries_cafe_staffing_policy(session: Session) -> None:
    context = build_planning_context(session, "CAFE001")
    staffing = context.reference["cafe_policies"]["staffing"]
    assert staffing["busy_event_required_staff"] == 4
    assert "barista" in staffing["espresso_roles"]
