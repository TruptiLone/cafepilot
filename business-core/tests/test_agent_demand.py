"""Demand from history, normal use in the order math, and the validator rule that ties them."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine
from backend.modules.agent.demand import EventDemandInput, estimate_event_demand
from backend.modules.agent.planner import Plan
from backend.modules.agent.registry import REGISTRY
from backend.modules.agent.validator import validate_plan
from backend.modules.events.models import CafeEvent
from backend.modules.inventory.models import StockCount, StockCountLine
from backend.modules.sales.models import Sale, SaleItem
from scripts.seed_sales_catalog import catalog_identifier
from tests.sales_v2_support import seed_sales_v2_test_catalog

CAFE = "TEST_AGENT_DEMAND_CAFE"
TZ = ZoneInfo("America/Los_Angeles")
UPCOMING = date(2026, 11, 1)  # Sunday
AS_OF = datetime(2026, 10, 29, 18, tzinfo=TZ)
NORMAL, BUSY = 10, 15  # lattes sold 8 AM-2 PM on normal and event Sundays


def _id(local: str) -> str:
    return catalog_identifier(CAFE, local)


@pytest.fixture
def session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    db = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        seed_sales_v2_test_catalog(db, CAFE)
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def _sell(db: Session, day: date, lattes: int, hour: int = 9) -> None:
    sale_id = f"TDM_{day:%Y%m%d}_{hour}"
    at = datetime.combine(day, datetime.min.time(), TZ).replace(hour=hour)
    db.add(Sale(sale_id=sale_id, cafe_id=CAFE, source_id=sale_id, source="generated", external_id=None,
                sold_at=at, recorded_at=at, status="completed", subtotal_cents=500 * lattes,
                discount_cents=0, tax_cents=0, total_cents=500 * lattes))
    db.flush()
    db.add(SaleItem(sale_item_id=f"{sale_id}_1", sale_id=sale_id, cafe_id=CAFE, product_id=_id("COF003"),
                    variation_id=_id("COF003_REG"), product_name="Latte", variation_name="Regular 12 oz",
                    quantity=lattes, unit_price_cents=500, modifier_unit_cents=0, line_total_cents=500 * lattes))


def _event(db: Session, event_id: str, day: date, *, name="Market", status="happened", kind="farmers_market") -> None:
    db.add(CafeEvent(event_id=event_id, cafe_id=CAFE, name=name, event_type=kind,
                     starts_at=datetime.combine(day, datetime.min.time(), TZ).replace(hour=8),
                     ends_at=datetime.combine(day, datetime.min.time(), TZ).replace(hour=14),
                     status=status, source="test", is_synthetic=True))


def _history(db: Session, event_sundays: int) -> None:
    """12 Sundays before the upcoming one; up to 3 of them are markets (each with earlier normal Sundays)."""
    sundays = [UPCOMING - timedelta(weeks=w) for w in range(12, 0, -1)]
    markets = [sundays[4], sundays[7], sundays[10]][:event_sundays]
    for day in sundays:
        _sell(db, day, BUSY if day in markets else NORMAL)
    for i, day in enumerate(markets):
        _event(db, f"TDM_EVT_{i}", day)
    _event(db, "TDM_UPCOMING", UPCOMING, status="upcoming")
    db.flush()


def _estimate(db: Session):
    return estimate_event_demand(db, EventDemandInput(cafe_id=CAFE, event_id="TDM_UPCOMING", as_of=AS_OF))


def test_normal_day_ignores_event_days_and_effect_comes_from_past_events(session: Session) -> None:
    _history(session, event_sundays=3)
    result = _estimate(session)
    assert result.confidence == "good" and len(result.examples) == 3
    assert result.drinks.normal == NORMAL  # event Sundays are excluded from the normal day
    # ratio with smoothing 3: (15 + 3) / (10 + 3); applied to a normal 10 -> about +4
    assert result.drinks.extra_typical == round(NORMAL * ((BUSY + 3) / (NORMAL + 3) - 1))
    assert result.scenarios["typical"].items[0].variation_id == _id("COF003_REG")
    assert "average of the last 8 Sundays" in result.normal_rule


def test_one_example_is_a_rough_guide_and_none_shows_a_normal_day(session: Session) -> None:
    _history(session, event_sundays=1)
    assert _estimate(session).confidence == "rough"
    session.query(CafeEvent).filter(CafeEvent.event_id == "TDM_EVT_0").delete()
    session.flush()
    result = _estimate(session)
    assert result.confidence == "none" and result.drinks.extra_typical == 0
    assert result.scenarios["typical"].items == []
    assert "No past farmers market events" in result.notes[0]


def test_past_editions_of_the_same_event_are_preferred(session: Session) -> None:
    _history(session, event_sundays=3)
    session.query(CafeEvent).filter(CafeEvent.event_id == "TDM_UPCOMING").update({"name": "Market"})
    _event(session, "TDM_OTHER_KIND", UPCOMING - timedelta(weeks=6), name="Other fair")
    session.flush()
    assert {e.name for e in _estimate(session).examples} == {"Market"}


def test_order_math_counts_normal_use_before_the_event(session: Session) -> None:
    _history(session, event_sundays=3)
    counted = datetime(2026, 10, 29, 17, tzinfo=TZ)
    session.add(StockCount(count_id="TDM_COUNT", cafe_id=CAFE, counted_at=counted, recorded_at=counted,
                           counted_by=None, status="verified", is_synthetic=True))
    session.flush()
    session.add(StockCountLine(count_line_id="TDM_LINE", count_id="TDM_COUNT", cafe_id=CAFE,
                               ingredient_id=_id("WHOLE_MILK"), counted_quantity=Decimal("5000")))
    session.flush()
    items = [{"variation_id": _id("COF003_REG"), "quantity": 4}]
    base = {"cafe_id": CAFE, "as_of": AS_OF.isoformat(), "target_at": "2026-11-01T08:00:00-08:00",
            "items": items, "buffer_pct": "0"}
    without = REGISTRY["calculate_shortfall"].run(session, base)
    with_use = REGISTRY["calculate_shortfall"].run(session, {**base, "include_normal_use": True})
    milk = lambda r: next(l for l in r["lines"] if l["ingredient_id"] == _id("WHOLE_MILK"))  # noqa: E731
    assert milk(without)["status"] == "enough"  # 4 lattes x 240 mL < 5 L on hand
    line = milk(with_use)
    # history only has Sunday sales, so normal use is the normal Sunday until close: 10 lattes
    assert Decimal(line["normal_use"]) == Decimal("2400")
    assert Decimal(line["available"]) == Decimal("2600")
    assert line["status"] == "enough" and with_use["normal_items"] == NORMAL
    with_use_more = REGISTRY["calculate_shortfall"].run(
        session, {**base, "include_normal_use": True, "items": [{**items[0], "quantity": 20}]}
    )
    assert milk(with_use_more)["order_quantity"] == "2200"  # 4,800 needed - 2,600 available


def _node(node_id: str, action: str, args: dict) -> dict:
    return {"id": node_id, "action": action, "args": {"cafe_id": CAFE, **args}, "reason": "r"}


def test_demand_must_come_from_history_when_the_plan_reads_it() -> None:
    demand = _node("demand", "estimate_event_demand", {"event_id": "E", "as_of": AS_OF.isoformat()})
    short = _node("short", "calculate_shortfall", {"as_of": AS_OF.isoformat(), "target_at": "2026-11-01T08:00:00-08:00",
                                                    "items": [{"variation_id": "X", "quantity": 40}]})
    plan = Plan.model_validate({"status": "plan", "goal": "g", "nodes": [demand, short]})
    assert [i.code for i in validate_plan(plan, CAFE).issues] == ["unverified_demand"]
    short["args"]["items"] = "$demand.scenarios.typical.items"
    assert validate_plan(Plan.model_validate({"status": "plan", "goal": "g", "nodes": [demand, short]}), CAFE).valid
