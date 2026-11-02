"""Ask Muse chat: Spark only routes; code runs read-only lookups and writes every number."""

import json
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.agent import planner as planner_module
from backend.modules.agent.chat import time_windows
from backend.modules.agent.demand import EventDemandInput, estimate_event_demand
from backend.modules.agent.router import get_chat_client
from backend.modules.inventory.models import InventoryItem
from backend.modules.inventory.service import InventoryService
from backend.modules.purchasing.models import PurchaseOrder
from backend.modules.sales.service import SalesService
from backend.modules.staffing.service import StaffingService
from scripts.seed_event_surge import apply_reset, plan_reset, seed_event_surge
from zoneinfo import ZoneInfo

C = "CAFE001"
TZ = ZoneInfo("America/Los_Angeles")
AS_OF = "2026-10-29T18:00:00-07:00"
NOW = datetime.fromisoformat(AS_OF)
WINDOWS = time_windows(NOW, TZ)
SUNDAY = WINDOWS["days"]["sunday 2026-11-01"]
MARKET = "EVT_SC_FARMERS_MARKET_20261101"


class Router:
    """Stands in for Muse Spark: returns the routing it is given and records the call."""

    def __init__(self, routing: dict | str | Exception) -> None:
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._routing = routing

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._routing, Exception):
            raise self._routing
        content = self._routing if isinstance(self._routing, str) else json.dumps(self._routing)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)


@pytest.fixture
def session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    db = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        if db.get(Cafe, C) is None:
            pytest.skip("demo data not seeded; run scripts/seed_all.sh")
        apply_reset(db, plan_reset(db))
        seed_event_surge(db, with_delivery=True)
        db.commit()  # a savepoint: the outer transaction still rolls everything back
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def log_file(tmp_path, monkeypatch):
    path = tmp_path / "llm.jsonl"
    monkeypatch.setattr(planner_module, "LLM_LOG_PATH", path)
    return path


def ask(session: Session, message: str, routing, *, status: int = 200, as_of: str | None = AS_OF) -> dict:
    router = routing if isinstance(routing, Router) else Router(routing)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_chat_client] = lambda: router
    try:
        body = {"message": message, "channel": "text", **({"as_of": as_of} if as_of else {})}
        response = TestClient(app).post("/api/v1/agent/chat", json=body)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == status, response.text
    return response.json()


def answer(*actions: tuple[str, dict]) -> dict:
    return {"intent": "answer", "actions": [{"action": a, "args": args} for a, args in actions]}


def estimates(session: Session) -> dict[str, object]:
    return {e.ingredient_id: e for e in InventoryService(session).list_stock_estimates(C, None, NOW)}


def num(value) -> str:
    text = f"{value}"
    return text.rstrip("0").rstrip(".") if "." in text else text


# ---------- stock -------------------------------------------------------------


def test_stock_for_one_item_comes_from_the_estimate(session: Session) -> None:
    body = ask(session, "do you have blueberry muffins", answer(("get_stock_estimates", {"ingredient_id": "BLUEBERRY_MUFFIN"})))
    truth = estimates(session)["BLUEBERRY_MUFFIN"]
    [row] = body["rows"]
    assert (row["name"], row["quantity"], row["unit"]) == ("Blueberry Muffin", num(truth.estimated_quantity), "each")
    assert row["as_of"] == AS_OF
    assert body["lines"][0] == f"Blueberry Muffin {num(truth.estimated_quantity)} on hand. Nothing here is below its reorder point."
    assert body["lookups"][0]["args"]["as_of"] == AS_OF  # the reference time, filled in by code


def test_stock_for_several_items_and_estimated_flag(session: Session) -> None:
    body = ask(session, "how much oat and whole milk", answer(("get_stock_estimates", {"ingredient_ids": ["OAT_MILK", "WHOLE_MILK"]})))
    truth = estimates(session)
    rows = {r["name"]: r for r in body["rows"]}
    assert rows["Oat Milk"]["quantity"] == num(truth["OAT_MILK"].estimated_quantity)
    assert rows["Whole Milk"]["quantity"] == num(truth["WHOLE_MILK"].estimated_quantity)
    assert rows["Oat Milk"]["estimated"] is True  # count minus the logged spill
    assert rows["Oat Milk"]["unit"] == "mL"


def test_low_means_below_the_reorder_point_in_the_database(session: Session) -> None:
    body = ask(session, "are we low on anything", answer(("get_stock_estimates", {})))
    points = dict(session.execute(select(InventoryItem.ingredient_id, InventoryItem.reorder_point)
                                  .where(InventoryItem.cafe_id == C, InventoryItem.reorder_point.is_not(None))).all())
    truth = estimates(session)
    low = sorted(e.name for i, e in truth.items()
                 if i in points and e.stock_status == "known" and e.estimated_quantity < points[i])
    assert low == ["Banana Nut Muffin", "Oat Milk"]  # the demo seed puts these two below
    flagged = sorted(r["name"] for r in body["rows"] if "below reorder point" in (r["detail"] or ""))
    assert flagged == low
    # The low items lead; no count of everything, and the unknown item is a note at the end.
    assert body["lines"] == ["Below reorder point: Banana Nut Muffin (9 left, reorder at 12); Oat Milk (1.25 L left, reorder at 3 L)."]
    unsure = sorted(e.name for e in truth.values() if e.stock_status != "known")
    assert body["notes"] == [f"Not sure about {', '.join(unsure)}."]


def test_one_low_item_is_answered_by_its_reorder_line(session: Session) -> None:
    body = ask(session, "do you have banana nut muffins", answer(("get_stock_estimates", {"ingredient_id": "BANANA_NUT_MUFFIN"})))
    assert body["lines"] == ["Below reorder point: Banana Nut Muffin (9 left, reorder at 12)."]
    assert [(r["name"], r["quantity"]) for r in body["rows"]] == [("Banana Nut Muffin", num(estimates(session)["BANANA_NUT_MUFFIN"].estimated_quantity))]


def test_without_reorder_points_low_is_not_guessed(session: Session) -> None:
    for item in session.scalars(select(InventoryItem).where(InventoryItem.cafe_id == C)):
        item.reorder_point = None  # rolled back with the test
    session.commit()
    body = ask(session, "are we low on anything", answer(("get_stock_estimates", {})))
    assert body["lines"] == ["No reorder points are set in the stock list, so I can't tell what's low."]
    assert not any("below reorder point" in (r["detail"] or "") for r in body["rows"])


def test_unknown_item_is_not_on_the_stock_list(session: Session) -> None:
    body = ask(session, "do we have unicorn cake", answer(("get_stock_estimates", {"ingredient_id": "UNICORN_CAKE"})))
    assert body["lines"] == ["That's not on our stock list."]
    assert body["rows"] == []


# ---------- sales ---------------------------------------------------------------


def test_sales_today_match_the_sales_report(session: Session) -> None:
    today = WINDOWS["today"]
    body = ask(session, "how many lattes did we sell today", answer(("sales_summary", today)))
    report = SalesService(session).report(C, datetime.fromisoformat(today["start_at"]), datetime.fromisoformat(today["end_at"]))
    assert [(r["name"], r["quantity"]) for r in body["rows"]] == [(p.name, str(p.quantity)) for p in report.top_products]
    assert body["lines"][0].startswith(f"{report.sales_count} sales.")


def test_sales_this_week_match_the_sales_report(session: Session) -> None:
    week = WINDOWS["this_week"]
    body = ask(session, "how were sales this week", answer(("sales_summary", week)))
    report = SalesService(session).report(C, datetime.fromisoformat(week["start_at"]), datetime.fromisoformat(week["end_at"]))
    assert body["lines"][0].startswith(f"{report.sales_count} sales.")
    assert [r["quantity"] for r in body["rows"]] == [str(p.quantity) for p in report.top_products]


# ---------- staff ---------------------------------------------------------------


def test_who_is_working_sunday_morning(session: Session) -> None:
    morning = SUNDAY["morning"]
    body = ask(session, "who's working Sunday morning", answer(("list_shifts", morning)))
    shifts = StaffingService(session).list_shifts(C, datetime.fromisoformat(morning["start_at"]), datetime.fromisoformat(morning["end_at"]))
    assert sorted(r["name"] for r in body["rows"]) == sorted(s.employee_name for s in shifts if s.status == "scheduled")
    assert body["lines"][0].startswith(f"{len([s for s in shifts if s.status == 'scheduled'])} shift")


def test_staff_list(session: Session) -> None:
    body = ask(session, "who works here", answer(("list_employees", {})))
    employees = [e for e in StaffingService(session).list_employees(C) if e.active]
    assert sorted(r["name"] for r in body["rows"]) == sorted(e.name for e in employees)


def test_event_coverage_numbers_come_from_the_coverage_report(session: Session) -> None:
    window = {"start_at": "2026-11-01T08:00:00-08:00", "end_at": "2026-11-01T14:00:00-08:00", "required_staff": 4}
    body = ask(session, "are we covered for the market", answer(("coverage_report", window)))
    report = StaffingService(session).coverage_report(C, datetime.fromisoformat(window["start_at"]),
                                                     datetime.fromisoformat(window["end_at"]), 4)
    assert [r["quantity"] for r in body["rows"]] == [str(h.scheduled_employees) for h in report.hours]


# ---------- orders, events, recipes ---------------------------------------------------


def test_when_does_the_milk_arrive(session: Session) -> None:
    order = session.scalar(select(PurchaseOrder).where(PurchaseOrder.cafe_id == C, PurchaseOrder.status != "cancelled"))
    body = ask(session, "when does the milk arrive", answer(("get_purchase_order", {"order_id": order.order_id})))
    assert sorted((r["quantity"], r["unit"]) for r in body["rows"]) == sorted((num(l.quantity), l.unit) for l in order.lines)
    assert all("delivery Sun Nov 1, 5:30 AM" in r["detail"] for r in body["rows"])


def test_deliveries_list_the_open_order_and_regular_delivery(session: Session) -> None:
    from backend.modules.agent.cafe_policies import policies_for

    body = ask(session, "when does the milk arrive", answer(("list_deliveries", {"ingredient_id": "WHOLE_MILK"})))
    order = session.scalar(select(PurchaseOrder).where(PurchaseOrder.cafe_id == C, PurchaseOrder.po_number == "PO-1042"))
    po_line = next(l for l in order.lines if l.ingredient_id == "WHOLE_MILK")
    regular = next(r for r in policies_for(C)["standing_deliveries"] if r["ingredient_id"] == "WHOLE_MILK")
    rows = [(r["quantity"], r["unit"], r["detail"]) for r in body["rows"]]
    assert rows == [
        (regular["quantity"], regular["unit"], "Fri Oct 30, 6 AM · regular delivery"),
        (num(po_line.quantity), po_line.unit, "Sun Nov 1, 5:30 AM · Willow Ridge Dairy Supply PO-1042, confirmed"),
    ]
    assert body["lines"] == ["Next: whole milk 12 L Fri Oct 30 6 AM (regular delivery), "
                             "whole milk 12 L Sun Nov 1 5:30 AM (Willow Ridge Dairy Supply PO-1042, confirmed)."]
    assert body["lookups"][0]["args"]["start_at"] == AS_OF  # the reference time, filled in by code


def test_deliveries_say_plainly_when_no_order_is_open(session: Session) -> None:
    session.delete(session.scalar(select(PurchaseOrder).where(PurchaseOrder.po_number == "PO-1042")))
    session.commit()  # rolled back with the test
    body = ask(session, "when does the milk arrive", answer(("list_deliveries", {"ingredient_id": "WHOLE_MILK"})))
    assert body["lines"] == ["No supplier orders are open. Next: whole milk 12 L Fri Oct 30 6 AM (regular delivery)."]


def test_deliveries_for_an_unknown_item(session: Session) -> None:
    body = ask(session, "when do the unicorns arrive", answer(("list_deliveries", {"ingredient_id": "UNICORN"})))
    assert body["lines"] == ["That's not on our stock list."]


def test_open_orders_are_in_the_reference_for_the_router(session: Session) -> None:
    router = Router({"intent": "answer", "actions": []})
    ask(session, "when does the milk arrive", router)
    reference = json.loads(router.calls[0]["messages"][-1]["content"])["reference"]
    assert [o["po_number"] for o in reference["open_orders"]] == ["PO-1042"]
    assert reference["open_orders"][0]["delivery_at"].endswith("-08:00")  # café time


def test_event_demand_comes_from_the_history(session: Session) -> None:
    body = ask(session, "how busy will the market be", answer(("estimate_event_demand", {"event_id": MARKET, "as_of": AS_OF})))
    demand = estimate_event_demand(session, EventDemandInput(cafe_id=C, event_id=MARKET, as_of=NOW))
    rows = {r["name"]: r for r in body["rows"]}
    assert rows["Extra drinks (typical)"]["quantity"] == str(demand.drinks.extra_typical)
    assert rows["Extra pastries (typical)"]["quantity"] == str(demand.food.extra_typical)
    assert str(demand.drinks.extra_typical) in body["lines"][0]


def test_recipe_usage_for_twenty_lattes(session: Session) -> None:
    from backend.modules.agent.registry import REGISTRY

    args = {"target_at": AS_OF, "items": [{"variation_id": "COF003_REG", "quantity": 20}]}
    body = ask(session, "how much milk do 20 lattes take", answer(("project_recipe_usage", args)))
    truth = REGISTRY["project_recipe_usage"].run(session, {"cafe_id": C, **args})
    assert [(r["name"], r["quantity"]) for r in body["rows"]] == [(i["name"], num(Decimal(str(i["quantity"])))) for i in truth["ingredients"]]


# ---------- never writes, scope, errors --------------------------------------------------


def test_a_write_request_goes_to_the_plan_flow(session: Session) -> None:
    body = ask(session, "order more oat milk", {"intent": "needs_plan", "actions": []})
    assert body["intent"] == "needs_plan" and body["plan_request"] == "order more oat milk"
    assert body["lookups"] == [] and body["lines"] == []


def test_a_write_action_from_the_model_is_never_run(session: Session) -> None:
    before = session.scalar(select(func.count()).select_from(PurchaseOrder))
    routing = answer(("propose_supplier_order", {"lines": [{"ingredient_id": "OAT_MILK", "quantity": "6000", "unit": "mL"}],
                                                 "requested_delivery_at": "2026-11-01T06:00:00-08:00"}))
    body = ask(session, "get us some oat milk", routing)
    assert body["intent"] == "needs_plan" and body["plan_request"] == "get us some oat milk"
    assert session.scalar(select(func.count()).select_from(PurchaseOrder)) == before


def test_an_unrelated_question_is_out_of_scope(session: Session) -> None:
    body = ask(session, "what's the capital of France", {"intent": "out_of_scope", "actions": []})
    assert body["intent"] == "out_of_scope" and body["lookups"] == []
    assert "not something I can help with" in body["lines"][0]


def test_bad_arguments_are_reported_not_guessed(session: Session) -> None:
    body = ask(session, "sales", answer(("sales_summary", {"start_at": "yesterday"})))
    assert body["lookups"][0]["error"] and body["rows"] == []
    assert body["lines"] == ["I couldn't look that up."]


def test_at_most_three_actions(session: Session) -> None:
    four = answer(*[("list_employees", {})] * 4)
    ask(session, "everything", four, status=502)


def test_every_call_is_logged_with_model_and_latency(session: Session, log_file) -> None:
    body = ask(session, "who works here", answer(("list_employees", {})))
    [record] = [json.loads(line) for line in log_file.read_text().splitlines()]
    assert (record["purpose"], record["model"], record["attempt"]) == ("chat", body["model"], 1)
    assert record["latency_ms"] == body["latency_ms"] and isinstance(body["latency_ms"], int)
    assert body["model"] == "muse-spark-1.3"


def test_a_slow_model_times_out_with_a_clear_error(session: Session, log_file) -> None:
    body = ask(session, "who works here", Router(type("APITimeoutError", (Exception,), {})("t")), status=504)
    assert "within 10 seconds" in body["detail"]
    [record] = [json.loads(line) for line in log_file.read_text().splitlines()]
    assert record["purpose"] == "chat" and record["error"].startswith("APITimeoutError") and "latency_ms" in record


def test_unreadable_routing_is_a_clear_error(session: Session) -> None:
    body = ask(session, "hello?", "not json", status=502)
    assert "wasn't readable" in body["detail"]


def test_today_and_this_week_follow_the_reference_time() -> None:
    assert WINDOWS["today"] == {"start_at": "2026-10-29T00:00:00-07:00", "end_at": "2026-10-30T00:00:00-07:00"}
    assert WINDOWS["this_week"] == {"start_at": "2026-10-26T00:00:00-07:00", "end_at": "2026-11-02T00:00:00-08:00"}
    assert SUNDAY["morning"] == {"start_at": "2026-11-01T06:00:00-08:00", "end_at": "2026-11-01T12:00:00-08:00"}


def test_reference_time_defaults_to_now_like_plans(session: Session) -> None:
    router = Router({"intent": "answer", "actions": []})
    body = ask(session, "anything", router, as_of=None)
    now = datetime.fromisoformat(body["as_of"])
    assert abs((datetime.now(TZ) - now).total_seconds()) < 60
    assert json.loads(router.calls[0]["messages"][-1]["content"])["reference"]["cafe_timezone"] == "America/Los_Angeles"


def test_the_router_only_sees_read_actions(session: Session) -> None:
    router = Router({"intent": "answer", "actions": []})
    ask(session, "anything", router)
    offered = {a["name"] for a in json.loads(router.calls[0]["messages"][-1]["content"])["read_actions"]}
    assert "propose_supplier_order" not in offered and "schedule_shift" not in offered
    assert {"get_stock_estimates", "sales_summary", "list_shifts", "get_purchase_order", "list_deliveries"} <= offered
