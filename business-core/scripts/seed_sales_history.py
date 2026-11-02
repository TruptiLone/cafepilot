"""Synthetic sales history and event calendar for CAFE001 (demo data, clearly labeled).

Generates line-level sales (sizes, milk choices, extras) for May 1 – Oct 29 2026 and for
Sep 1 – Nov 2 2025 (so last Halloween has comparable normal Fridays), plus a calendar of past
events whose effect is built into those sales, and the upcoming events Muse will "detect".

The method CaféPilot uses to read event effects from history is real; the history itself is
invented. Never present results from this data as a validated forecast.

Usage (from business-core/, after ./scripts/seed_all.sh):
    PYTHONPATH=. python scripts/seed_sales_history.py           # seed (safe to repeat)
    PYTHONPATH=. python scripts/seed_sales_history.py --reset   # remove history + calendar
"""

from __future__ import annotations

import argparse
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from backend.db.session import SessionLocal
from backend.models.cafe import Cafe
from backend.modules.events.models import CafeEvent
from backend.modules.inventory import models as _inventory_models  # noqa: F401  (register mappers)
from backend.modules.staffing import models as _staffing_models  # noqa: F401
from backend.modules.sales.models import Sale, SaleItem, SaleItemModifier

CAFE_ID = "CAFE001"
SALE_PREFIX = "HIST_"
EVENT_PREFIX = "EVT_"
SOURCE = "Santa Clara community events (demo)"
PERIODS = ((date(2025, 9, 1), date(2025, 11, 2)), (date(2026, 5, 1), date(2026, 10, 29)))
LAST_SALE = datetime(2026, 10, 29, 17, 0)  # local; the Thursday closing count is at 5 PM

# ---- normal trading pattern (orders per hour) -------------------------------------
WEEKDAY_HOURS = {7: 14, 8: 18, 9: 14, 10: 9, 11: 8, 12: 9, 13: 8, 14: 7, 15: 6, 16: 5, 17: 4, 18: 3}
WEEKEND_HOURS = {7: 7, 8: 12, 9: 15, 10: 15, 11: 13, 12: 11, 13: 9, 14: 7, 15: 5, 16: 4}

# product_id -> (weight, category)
PRODUCTS = {
    "COF003": (18, "milk_coffee"), "COF004": (8, "milk_coffee"), "COF005": (7, "milk_coffee"),
    "COF006": (5, "milk_coffee"), "COF007": (5, "milk_coffee"), "COF001": (12, "black_coffee"),
    "COF002": (7, "black_coffee"), "COF008": (7, "cold"), "DRK001": (3, "hot_chocolate"),
    "DRK002": (5, "milk_other"), "DRK003": (4, "milk_other"), "DRK004": (3, "tea"),
    "BAK001": (3, "bakery"), "BAK002": (3, "bakery"), "BAK003": (2, "bakery"),
    "BAK004": (2, "bakery"), "BAK005": (2, "bakery"), "BAK006": (4, "bakery"),
}
MILK_CHOICES = (("MILK_WHOLE", 0.62), ("MILK_OAT", 0.25), ("MILK_SKIM", 0.06), ("MILK_ALMOND", 0.07))
EXTRA_SHOT_RATE = 0.10


@dataclass(frozen=True)
class PastEvent:
    event_id: str
    name: str
    event_type: str
    day: date
    start: time
    end: time
    location: str
    distance_m: int | None
    recurrence: str
    notes: str


def _monthly_markets() -> list[PastEvent]:
    days = [date(2026, 5, 3), date(2026, 6, 7), date(2026, 7, 5), date(2026, 8, 2), date(2026, 9, 6), date(2026, 10, 4)]
    return [
        PastEvent(f"EVT_FARMERS_MARKET_{d:%Y%m%d}", "Santa Clara Farmers Market", "farmers_market", d, time(8), time(14),
                  "Franklin Square, Santa Clara", 250, "monthly", "First-Sunday market two blocks away")
        for d in days
    ]


PAST_EVENTS: list[PastEvent] = [
    *_monthly_markets(),
    PastEvent("EVT_HALLOWEEN_20251031", "Halloween", "holiday", date(2025, 10, 31), time(15), time(19),
              "Downtown Santa Clara", None, "yearly", "Trick-or-treat evening; quieter afternoon"),
    PastEvent("EVT_INDEPENDENCE_DAY_20260704", "Independence Day", "holiday", date(2026, 7, 4), time(7), time(17),
              "Citywide", None, "yearly", "Public holiday"),
    PastEvent("EVT_LABOR_DAY_20260907", "Labor Day", "holiday", date(2026, 9, 7), time(7), time(19),
              "Citywide", None, "yearly", "Public holiday"),
    PastEvent("EVT_SUMMER_FESTIVAL_20260620", "Downtown Summer Festival", "community_festival", date(2026, 6, 20),
              time(10), time(17), "Franklin Street", 400, "yearly", "Street festival with live music"),
    PastEvent("EVT_ART_WINE_FESTIVAL_20260919", "Art & Wine Festival", "community_festival", date(2026, 9, 19),
              time(10), time(17), "Central Park, Santa Clara", 1200, "yearly", "Weekend festival"),
    *[
        PastEvent(f"EVT_FOOTBALL_{d:%Y%m%d}", "High school football home game", "sports", d, time(17), time(19),
                  "Santa Clara High School", 900, "weekly", "Friday night home game")
        for d in (date(2026, 9, 11), date(2026, 9, 25), date(2026, 10, 9), date(2026, 10, 23))
    ],
    *[
        PastEvent(f"EVT_HEAT_WAVE_{d:%Y%m%d}", "Heat wave", "weather", d, time(7), time(17 if d.weekday() >= 5 else 19),
                  "Santa Clara", None, "one_off", "Above 95 °F")
        for d in (date(2026, 7, 14), date(2026, 7, 15), date(2026, 7, 16), date(2026, 8, 18), date(2026, 8, 19))
    ],
]

UPCOMING = [
    dict(event_id="EVT_HALLOWEEN_20261031", name="Halloween", event_type="holiday",
         starts_at=datetime(2026, 10, 31, 14), ends_at=datetime(2026, 10, 31, 17),
         location="Downtown Santa Clara", distance_m=None, recurrence="yearly",
         notes="Trick-or-treat afternoon; usually quieter"),
    dict(event_id="EVT_SC_FARMERS_MARKET_20261101", name="Santa Clara Farmers Market",
         event_type="farmers_market", starts_at=datetime(2026, 11, 1, 8), ends_at=datetime(2026, 11, 1, 14),
         location="Franklin Square, Santa Clara", distance_m=250, recurrence="monthly",
         notes="First-Sunday market two blocks away"),
]


def _effect(event_type: str, category: str, hour: int, event: PastEvent) -> float:
    """Built-in effect of each past event on sales (the 'truth' the history encodes)."""
    if not (event.start.hour <= hour < event.end.hour):
        return 1.0
    table = {
        "farmers_market": {"milk_coffee": 1.55, "black_coffee": 1.35, "cold": 1.5, "milk_other": 1.3, "bakery": 1.6},
        "community_festival": {"milk_coffee": 1.35, "black_coffee": 1.2, "cold": 1.7, "bakery": 1.3, "milk_other": 1.2},
        "sports": {"hot_chocolate": 2.5, "milk_coffee": 1.5, "black_coffee": 1.6, "milk_other": 1.3},
        "weather": {"cold": 2.2, "milk_coffee": 0.8, "black_coffee": 0.75, "hot_chocolate": 0.5, "tea": 0.8},
    }
    if event_type == "holiday":
        if event.name == "Halloween":
            return 1.8 if category == "hot_chocolate" else 0.7
        return 0.65 if event.name == "Independence Day" else 0.8
    return table.get(event_type, {}).get(category, 1.0)


def _poisson(rng: random.Random, lam: float) -> int:
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _pick(rng: random.Random, weighted: list[tuple[str, float]]) -> str:
    total = sum(w for _, w in weighted)
    roll, acc = rng.random() * total, 0.0
    for key, weight in weighted:
        acc += weight
        if roll <= acc:
            return key
    return weighted[-1][0]


def _days() -> list[date]:
    out = []
    for start, end in PERIODS:
        d = start
        while d <= end:
            out.append(d)
            d += timedelta(days=1)
    return out


def _load_catalog(session: Session) -> tuple[dict, dict]:
    from backend.modules.sales.models import MenuProduct, Modifier, ProductVariation

    variations = {}
    for v in session.scalars(select(ProductVariation).where(ProductVariation.cafe_id == CAFE_ID)):
        product = session.get(MenuProduct, v.product_id)
        variations[v.variation_id] = (v, product)
    modifiers = {m.modifier_id: m for m in session.scalars(select(Modifier).where(Modifier.cafe_id == CAFE_ID))}
    return variations, modifiers


def _generate_day(day: date, tz: ZoneInfo, rng: random.Random, variations: dict, modifiers: dict,
                  events: list[PastEvent], tax_bps: int) -> tuple[list[dict], list[dict], list[dict]]:
    weekend = day.weekday() >= 5
    hours = WEEKEND_HOURS if weekend else WEEKDAY_HOURS
    todays = [e for e in events if e.day == day]
    summer = day.month in (6, 7, 8)
    day_factor = math.exp(rng.gauss(0, 0.07))
    sales, items, mods = [], [], []
    n = 0
    for hour, rate in hours.items():
        if datetime.combine(day, time(hour)) >= LAST_SALE:
            break
        # weights for this hour, after event effects
        weighted = []
        for product_id, (weight, category) in PRODUCTS.items():
            w = weight * (1.5 if summer and category == "cold" else 1.0)
            for event in todays:
                w *= _effect(event.event_type, category, hour, event)
            weighted.append((product_id, w))
        base_total = sum(PRODUCTS[p][0] * (1.5 if summer and PRODUCTS[p][1] == "cold" else 1.0) for p in PRODUCTS)
        volume = sum(w for _, w in weighted) / base_total
        oat_bonus = 0.08 if any(e.event_type == "farmers_market" and e.start.hour <= hour < e.end.hour for e in todays) else 0
        orders = _poisson(rng, rate * day_factor * volume)
        for _ in range(orders):
            n += 1
            sold_at = datetime.combine(day, time(hour, rng.randrange(60), rng.randrange(60)), tz)
            sale_id = f"{SALE_PREFIX}SALE_{day:%Y%m%d}_{n:04d}"
            count = 1 if rng.random() < 0.7 else (2 if rng.random() < 0.83 else 3)
            subtotal = 0
            for i in range(1, count + 1):
                product_id = _pick(rng, weighted)
                size = "REG" if product_id.startswith("BAK") or rng.random() < 0.65 else "LRG"
                variation_id = f"{product_id}_{size}"
                if variation_id not in variations:
                    variation_id = f"{product_id}_REG"
                variation, product = variations[variation_id]
                category = PRODUCTS[product_id][1]
                chosen: list[str] = []
                if category in ("milk_coffee", "milk_other", "hot_chocolate"):
                    oat = 0.25 + oat_bonus
                    milk = _pick(rng, [("MILK_WHOLE", 0.62 - oat_bonus), ("MILK_OAT", oat), ("MILK_SKIM", 0.06), ("MILK_ALMOND", 0.07)])
                    if milk != "MILK_WHOLE":
                        chosen.append(milk)
                    if category == "milk_coffee" and rng.random() < EXTRA_SHOT_RATE:
                        chosen.append("EXTRA_SHOT")
                quantity = 2 if rng.random() < 0.04 else 1
                modifier_unit = sum(modifiers[m].price_cents for m in chosen)
                line_total = quantity * (variation.price_cents + modifier_unit)
                subtotal += line_total
                item_id = f"{sale_id}_{i}"
                items.append(dict(sale_item_id=item_id, sale_id=sale_id, cafe_id=CAFE_ID, product_id=product.product_id,
                                  variation_id=variation_id, product_name=product.name, variation_name=variation.name,
                                  quantity=quantity, unit_price_cents=variation.price_cents,
                                  modifier_unit_cents=modifier_unit, line_total_cents=line_total))
                for m in chosen:
                    mods.append(dict(sale_item_modifier_id=f"{item_id}_{m}", cafe_id=CAFE_ID, sale_item_id=item_id,
                                     modifier_id=m, modifier_name=modifiers[m].name,
                                     price_cents=modifiers[m].price_cents, quantity=1))
            tax = round(subtotal * tax_bps / 10000)
            sales.append(dict(sale_id=sale_id, cafe_id=CAFE_ID, source_id=f"{SALE_PREFIX}POS_{day:%Y%m%d}_{n:04d}",
                              source="generated", external_id=None, sold_at=sold_at,
                              recorded_at=sold_at + timedelta(minutes=1), status="completed",
                              subtotal_cents=subtotal, discount_cents=0, tax_cents=tax, total_cents=subtotal + tax))
    return sales, items, mods


def seed_events(session: Session, tz: ZoneInfo) -> int:
    created = 0
    rows = [
        dict(event_id=e.event_id, cafe_id=CAFE_ID, name=e.name, event_type=e.event_type,
             starts_at=datetime.combine(e.day, e.start, tz), ends_at=datetime.combine(e.day, e.end, tz),
             location=e.location, distance_m=e.distance_m, expected_attendance=None, attendance_source=None,
             recurrence=e.recurrence, status="happened", source=SOURCE, source_url=None, is_synthetic=True,
             detected_at=None, notes=e.notes)
        for e in PAST_EVENTS
    ] + [
        dict(**{k: v for k, v in u.items() if k not in ("starts_at", "ends_at")}, cafe_id=CAFE_ID,
             starts_at=u["starts_at"].replace(tzinfo=tz), ends_at=u["ends_at"].replace(tzinfo=tz),
             expected_attendance=None, attendance_source=None, status="upcoming", source=SOURCE,
             source_url=None, is_synthetic=True, detected_at=datetime(2026, 10, 29, 18, 0, tzinfo=tz))
        for u in UPCOMING
    ]
    for row in rows:
        if session.get(CafeEvent, row["event_id"]) is None:
            session.add(CafeEvent(**row))
            created += 1
    session.flush()
    return created


def seed_sales_history(session: Session, *, days: list[date] | None = None) -> tuple[int, int, int]:
    cafe = session.get(Cafe, CAFE_ID)
    if cafe is None:
        raise RuntimeError("CAFE001 is missing: run ./scripts/seed_all.sh first")
    tz = ZoneInfo(cafe.timezone)
    variations, modifiers = _load_catalog(session)
    events_created = seed_events(session, tz)
    rng = random.Random(20261101)
    created_sales = 0
    for day in days or _days():
        day_sales, day_items, day_mods = _generate_day(day, tz, rng, variations, modifiers, PAST_EVENTS,
                                                      cafe.tax_rate_bps or 0)
        exists = session.scalar(
            select(func.count()).select_from(Sale).where(Sale.sale_id.startswith(f"{SALE_PREFIX}SALE_{day:%Y%m%d}_"))
        )
        if exists:
            continue  # already seeded; RNG still advanced so later days stay identical
        if day_sales:
            session.execute(insert(Sale), day_sales)
            session.execute(insert(SaleItem), day_items)
            if day_mods:
                session.execute(insert(SaleItemModifier), day_mods)
            created_sales += len(day_sales)
    session.flush()
    items = session.scalar(select(func.count()).select_from(SaleItem).where(SaleItem.sale_id.startswith(SALE_PREFIX)))
    return events_created, created_sales, items


def reset(session: Session) -> None:
    item_ids = select(SaleItem.sale_item_id).where(SaleItem.sale_id.startswith(SALE_PREFIX))
    session.execute(delete(SaleItemModifier).where(SaleItemModifier.sale_item_id.in_(item_ids)))
    session.execute(delete(SaleItem).where(SaleItem.sale_id.startswith(SALE_PREFIX)))
    session.execute(delete(Sale).where(Sale.sale_id.startswith(SALE_PREFIX)))
    session.execute(delete(CafeEvent).where(CafeEvent.cafe_id == CAFE_ID, CafeEvent.is_synthetic.is_(True)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="remove the synthetic history and calendar")
    args = parser.parse_args()
    with SessionLocal() as session:
        if args.reset:
            reset(session)
            session.commit()
            print("Synthetic sales history and event calendar removed.")
            return
        events, sales, items = seed_sales_history(session)
        session.commit()
    print(f"Sales history ready: {events} events and {sales} sales created ({items} history line items in total). "
          "Demo data, not a validated forecast.")


if __name__ == "__main__":
    main()
