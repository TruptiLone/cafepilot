from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.modules.agent.executor import (
    EditValidationError,
    create_run,
    decide,
    execute,
    get_run,
)
from backend.modules.agent.models import AgentApproval
from backend.modules.agent.planner import Plan
from backend.modules.agent.registry import REGISTRY
from backend.modules.agent.validator import validate_plan
from backend.modules.inventory.models import StockCount, StockCountLine
from backend.modules.staffing.models import Employee, Shift
from scripts.seed_sales_catalog import catalog_identifier
from tests.sales_v2_support import seed_sales_v2_test_catalog

CAFE = "TEST_AGENT_ORDER_CAFE"
TZ = "America/Los_Angeles"
AS_OF = "2026-10-29T18:00:00-07:00"
TARGET = "2026-11-01T08:00:00-08:00"


def _id(local: str) -> str:
    return catalog_identifier(CAFE, local)


@pytest.fixture
def session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    db = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        seed_sales_v2_test_catalog(db, CAFE)
        counted = datetime.fromisoformat("2026-10-29T17:00:00-07:00")
        db.add(StockCount(count_id="TOC_COUNT", cafe_id=CAFE, counted_at=counted, recorded_at=counted,
                          counted_by=None, status="verified", is_synthetic=True))
        db.flush()
        for local, qty in (("WHOLE_MILK", "8000"), ("OAT_MILK", "3750"), ("COFFEE_BEANS", "2500")):
            db.add(StockCountLine(count_line_id=f"TOC_{local}", count_id="TOC_COUNT", cafe_id=CAFE,
                                  ingredient_id=_id(local), counted_quantity=Decimal(qty)))
        created = datetime(2026, 10, 1, tzinfo=timezone.utc)
        db.add_all([
            Employee(employee_id="TOC_LEAD", cafe_id=CAFE, name="Alex", role="shift_lead", active=True, created_at=created),
            Employee(employee_id="TOC_SOFIA", cafe_id=CAFE, name="Sofia", role="barista", active=True, created_at=created),
            Employee(employee_id="TOC_ETHAN", cafe_id=CAFE, name="Ethan", role="barista", active=True, created_at=created),
        ])
        db.flush()
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def _items(whole: int = 40, oat: int = 20) -> list[dict]:
    return [
        {"variation_id": _id("COF003_REG"), "quantity": whole},
        {"variation_id": _id("COF003_REG"), "quantity": oat, "modifiers": [{"modifier_id": _id("MILK_OAT"), "quantity": 1}]},
    ]


def _shortfall(session: Session, **extra) -> dict:
    return REGISTRY["calculate_shortfall"].run(
        session, {"cafe_id": CAFE, "as_of": AS_OF, "target_at": TARGET, "items": _items(), **extra}
    )


# ---------- order math in code -------------------------------------------------


def test_shortfall_is_need_plus_buffer_minus_stock(session: Session) -> None:
    result = _shortfall(session, buffer_pct="10")
    lines = {line["ingredient_id"]: line for line in result["lines"]}
    whole = lines[_id("WHOLE_MILK")]
    assert (whole["needed"], whole["buffer"], whole["needed_with_buffer"]) == ("9600.000", "960.000", "10560.000")
    assert (whole["on_hand"], whole["order_quantity"], whole["status"]) == ("8000.000", "2560", "short")
    assert lines[_id("OAT_MILK")]["order_quantity"] == "1530"
    assert lines[_id("COFFEE_BEANS")]["status"] == "enough"
    assert {line["ingredient_id"] for line in result["order_lines"]} == {_id("WHOLE_MILK"), _id("OAT_MILK")}


def test_unknown_stock_is_reported_not_ordered(session: Session) -> None:
    result = REGISTRY["calculate_shortfall"].run(
        session,
        {"cafe_id": CAFE, "as_of": AS_OF, "target_at": TARGET,
         "items": [{"variation_id": _id("COF003_REG"), "quantity": 5,
                    "modifiers": [{"modifier_id": _id("MILK_ALMOND"), "quantity": 1}]}]},
    )
    assert result["unknown_stock"] == [_id("ALMOND_MILK")]
    assert all(line["ingredient_id"] != _id("ALMOND_MILK") for line in result["order_lines"])


def test_buffer_defaults_to_policy_and_can_be_zero(session: Session) -> None:
    assert _shortfall(session)["buffer_pct"] == "10"  # unknown café falls back to the default
    zero = {line["ingredient_id"]: line for line in _shortfall(session, buffer_pct="0")["lines"]}
    assert zero[_id("WHOLE_MILK")]["order_quantity"] == "1600"


# ---------- validator rules ------------------------------------------------------


def _plan(nodes: list[dict]) -> Plan:
    return Plan.model_validate({"status": "plan", "goal": "g", "nodes": nodes})


def _short_node(**args) -> dict:
    return {"id": "short", "action": "calculate_shortfall", "reason": "r",
            "args": {"cafe_id": CAFE, "as_of": AS_OF, "target_at": TARGET, "items": _items(), **args}}


def _order_node(lines) -> dict:
    return {"id": "order", "action": "propose_supplier_order", "reason": "r",
            "args": {"cafe_id": CAFE, "lines": lines, "requested_delivery_at": "2026-11-01T06:00:00-08:00"}}


def test_order_quantities_typed_by_the_model_are_rejected() -> None:
    plan = _plan([_short_node(), _order_node([{"ingredient_id": "X", "quantity": "12000", "unit": "mL"}])])
    result = validate_plan(plan, CAFE, timezone=TZ)
    assert {i.code for i in result.issues} == {"unverified_quantity"}


def test_order_linked_to_shortfall_is_accepted() -> None:
    result = validate_plan(_plan([_short_node(), _order_node("$short.order_lines")]), CAFE, timezone=TZ)
    assert result.valid, result.issues


def test_wrong_daylight_saving_offset_is_rejected_with_the_fix() -> None:
    order = _order_node("$short.order_lines")
    order["args"]["requested_delivery_at"] = "2026-11-01T06:00:00-07:00"  # Thursday's offset on Sunday
    result = validate_plan(_plan([_short_node(), order]), CAFE, timezone=TZ)
    assert [i.code for i in result.issues] == ["wrong_time_offset"]
    assert "2026-11-01T06:00:00-08:00" in result.issues[0].message


def test_offset_check_accepts_both_sides_of_the_switch() -> None:
    result = validate_plan(_plan([_short_node(), _order_node("$short.order_lines")]), CAFE, timezone=TZ)
    assert result.valid  # as_of uses -07:00 (Thu), target and delivery use -08:00 (Sun)


# ---------- executor: skip, run, and approve with changes -------------------------


def _start(session: Session, nodes: list[dict]) -> str:
    plan = _plan(nodes)
    validation = validate_plan(plan, CAFE, timezone=TZ)
    assert validation.valid, validation.issues
    run = create_run(session, query="q", cafe_id=CAFE, as_of=datetime.fromisoformat(AS_OF), plan=plan,
                     validation=validation, model="m", attempts=1)
    return execute(session, run.run_id).run_id


def _node(session: Session, run_id: str, node_id: str):
    return next(n for n in get_run(session, run_id).nodes if n.node_id == node_id)


def test_order_uses_the_computed_lines_and_waits(session: Session) -> None:
    run_id = _start(session, [_short_node(), _order_node("$short.order_lines")])
    order = _node(session, run_id, "order")
    assert order.status == "awaiting_approval"
    assert {(l["ingredient_id"], l["quantity"]) for l in order.resolved_args["lines"]} == {
        (_id("WHOLE_MILK"), "2560"), (_id("OAT_MILK"), "1530")}


def test_order_is_skipped_when_nothing_is_short(session: Session) -> None:
    short = _short_node()
    short["args"]["items"] = [{"variation_id": _id("COF003_REG"), "quantity": 1}]
    run_id = _start(session, [short, _order_node("$short.order_lines")])
    order = _node(session, run_id, "order")
    assert order.status == "skipped" and "Nothing to order" in order.error


def test_owner_can_change_quantities_before_approving(session: Session) -> None:
    run_id = _start(session, [_short_node(), _order_node("$short.order_lines")])
    edited_lines = [{"ingredient_id": _id("WHOLE_MILK"), "quantity": "3000", "unit": "mL"},
                    {"ingredient_id": _id("OAT_MILK"), "quantity": "2000", "unit": "mL"}]
    run = decide(session, run_id, "order", "approved", edited_args={"lines": edited_lines})
    order = next(n for n in run.nodes if n.node_id == "order")
    assert order.status == "done"
    by_id = lambda lines: {l["ingredient_id"]: l["quantity"] for l in lines}  # noqa: E731
    assert by_id(order.result["lines"]) == {_id("WHOLE_MILK"): "3000", _id("OAT_MILK"): "2000"}
    approval = session.scalar(select(AgentApproval).where(AgentApproval.run_id == run_id))
    assert by_id(approval.original_args["lines"])[_id("WHOLE_MILK")] == "2560"
    assert by_id(approval.edited_args["lines"])[_id("WHOLE_MILK")] == "3000"


def test_invalid_change_is_refused_and_step_keeps_waiting(session: Session) -> None:
    run_id = _start(session, [_short_node(), _order_node("$short.order_lines")])
    with pytest.raises(EditValidationError, match="not valid"):
        decide(session, run_id, "order", "approved",
               edited_args={"lines": [{"ingredient_id": _id("OAT_MILK"), "quantity": "-5", "unit": "mL"}]})
    session.rollback()
    assert _node(session, run_id, "order").status == "awaiting_approval"
    with pytest.raises(EditValidationError, match="only be made when approving"):
        decide(session, run_id, "order", "rejected", edited_args={"lines": []})


def _shift_nodes(employee: str) -> list[dict]:
    window = {"start_at": "2026-11-01T08:00:00-08:00", "end_at": "2026-11-01T14:00:00-08:00"}
    return [
        {"id": "cov", "action": "coverage_report", "reason": "r", "args": {"cafe_id": CAFE, **window, "required_staff": 4}},
        {"id": "staff", "action": "list_employees", "reason": "r", "args": {"cafe_id": CAFE}},
        {"id": "shifts", "action": "list_shifts", "reason": "r", "args": {"cafe_id": CAFE, **window}},
        {"id": "add", "action": "schedule_shift", "reason": "r",
         "args": {"cafe_id": CAFE, "employee_id": employee, "start_at": "2026-11-01T07:30:00-08:00",
                  "end_at": "2026-11-01T14:30:00-08:00"}},
    ]


def test_owner_can_swap_the_person_and_times(session: Session) -> None:
    run_id = _start(session, _shift_nodes("TOC_SOFIA"))
    decide(session, run_id, "add", "approved", edited_args={
        "employee_id": "TOC_ETHAN", "start_at": "2026-11-01T08:00:00-08:00", "cafe_id": "SOMEWHERE_ELSE"})
    shifts = session.scalars(select(Shift).where(Shift.cafe_id == CAFE)).all()
    assert [(s.employee_id, s.start_at.isoformat()) for s in shifts] == [("TOC_ETHAN", "2026-11-01T16:00:00+00:00")]


def test_api_returns_422_for_an_invalid_change(session: Session) -> None:
    run_id = _start(session, _shift_nodes("TOC_SOFIA"))
    app.dependency_overrides[get_db] = lambda: session
    try:
        response = TestClient(app).post(
            f"/api/v1/agent/runs/{run_id}/nodes/add/decision",
            json={"decision": "approved", "edited_args": {"end_at": "2026-11-01T06:00:00-08:00"}},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert "start_at must be before end_at" in response.json()["detail"]
