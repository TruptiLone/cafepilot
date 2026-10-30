"""Expected demand read from sales history, in code.

Normal day: for a date and hours, the average quantity of each product over the most recent
same-weekday dates (up to 8) that had no event.

Event effect: for each past event of the same type, compare what sold during its hours with
the normal day for that date and hours. Each comparison gives a ratio per product; applying
the ratios to the upcoming normal day gives one estimate of the extra demand per past event.
The lowest, middle and highest estimates become the low / typical / high scenarios.

With few past examples the result says so; with none it reports only the normal day. This is
a transparent average of comparable days, not a trained or validated forecast.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.models.cafe import Cafe
from backend.modules.events.models import CafeEvent
from backend.modules.sales.schemas import SaleItemV2Create

BASELINE_WEEKS = 8
SMOOTHING = 3.0  # added to both sides of a ratio so small groups don't swing wildly
Key = tuple[str, tuple[str, ...]]  # (variation_id, sorted modifier ids)


# ---------------------------------------------------------------------------
# History access
# ---------------------------------------------------------------------------


@dataclass
class History:
    """Quantities sold per local date, hour and product key."""

    tz: ZoneInfo
    by_day: dict[date, dict[int, dict[Key, int]]]
    names: dict[str, tuple[str, str, str]]  # variation_id -> (product_id, product_name, variation_name)
    categories: dict[str, str]  # variation_id -> menu category (coffee, other_drinks, bakery, ...)
    event_dates: set[date]

    def window(self, day: date, start_hour: int, end_hour: int) -> dict[Key, int]:
        totals: dict[Key, int] = defaultdict(int)
        for hour, items in self.by_day.get(day, {}).items():
            if start_hour <= hour < end_hour:
                for key, qty in items.items():
                    totals[key] += qty
        return totals

    def normal_dates(self, before: date, weekday: int, weeks: int = BASELINE_WEEKS) -> list[date]:
        out, day = [], before - timedelta(days=1)
        earliest = min(self.by_day) if self.by_day else before
        while len(out) < weeks and day >= earliest:
            if day.weekday() == weekday and day in self.by_day and day not in self.event_dates:
                out.append(day)
            day -= timedelta(days=1)
        return out

    def normal(self, day: date, start_hour: int, end_hour: int, weeks: int = BASELINE_WEEKS) -> tuple[dict[Key, float], list[date]]:
        dates = self.normal_dates(day, day.weekday(), weeks)
        windows = [self.window(d, start_hour, end_hour) for d in dates]
        keys = {k for w in windows for k in w}
        if not windows:
            return {}, dates
        return {k: sum(w.get(k, 0) for w in windows) / len(windows) for k in keys}, dates


_HISTORY_CACHE: dict[tuple, History] = {}


def load_history(session: Session, cafe_id: str, until: datetime) -> History:
    """Sales history before ``until``; cached while the sales before that time are unchanged."""
    cafe = session.get(Cafe, cafe_id)
    if cafe is None:
        raise LookupError(f"café not found: {cafe_id}")
    fingerprint = tuple(session.execute(
        text("SELECT count(*) FILTER (WHERE status = 'completed'), max(recorded_at), sum(total_cents) FROM sales WHERE cafe_id = :c AND sold_at < :u"),
        {"c": cafe_id, "u": until},
    ).one())
    key = (cafe_id, until, cafe.timezone, fingerprint)
    if key in _HISTORY_CACHE:
        return _HISTORY_CACHE[key]
    history = _load_history(session, cafe_id, until, ZoneInfo(cafe.timezone))
    if len(_HISTORY_CACHE) > 32:
        _HISTORY_CACHE.clear()
    _HISTORY_CACHE[key] = history
    return history


def _load_history(session: Session, cafe_id: str, until: datetime, tz: ZoneInfo) -> History:
    rows = session.execute(
        text(
            """
            SELECT s.sold_at, i.variation_id, i.product_id, i.product_name, i.variation_name, i.quantity, p.category,
                   COALESCE(string_agg(m.modifier_id, ',' ORDER BY m.modifier_id)
                            FILTER (WHERE m.modifier_id LIKE '%MILK\\_%' ESCAPE '\\'
                                      AND m.modifier_id NOT LIKE '%MILK\\_WHOLE' ESCAPE '\\'), '') AS milk
            FROM sales s
            JOIN sale_items i ON i.sale_id = s.sale_id
            JOIN menu_products p ON p.product_id = i.product_id
            LEFT JOIN sale_item_modifiers m ON m.sale_item_id = i.sale_item_id
            WHERE s.cafe_id = :cafe_id AND s.status = 'completed' AND s.sold_at < :until
            GROUP BY s.sold_at, i.sale_item_id, i.variation_id, i.product_id, i.product_name,
                     i.variation_name, i.quantity, p.category
            """
        ),
        {"cafe_id": cafe_id, "until": until},
    )
    by_day: dict[date, dict[int, dict[Key, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    names: dict[str, tuple[str, str, str]] = {}
    categories: dict[str, str] = {}
    for sold_at, variation_id, product_id, product_name, variation_name, quantity, category, milk in rows:
        local = sold_at.astimezone(tz)
        key: Key = (variation_id, tuple(m for m in milk.split(",") if m))
        by_day[local.date()][local.hour][key] += quantity
        names[variation_id] = (product_id, product_name, variation_name)
        categories[variation_id] = category

    event_dates: set[date] = set()
    for event in session.scalars(
        select(CafeEvent).where(CafeEvent.cafe_id == cafe_id, CafeEvent.status == "happened")
    ):
        start, end = event.starts_at.astimezone(tz).date(), event.ends_at.astimezone(tz).date()
        while start <= end:
            event_dates.add(start)
            start += timedelta(days=1)
    return History(tz=tz, by_day=by_day, names=names, categories=categories, event_dates=event_dates)


# ---------------------------------------------------------------------------
# The action
# ---------------------------------------------------------------------------


class EventDemandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cafe_id: str = "CAFE001"
    event_id: str = Field(description="An upcoming event from REFERENCE DATA upcoming_events.")
    as_of: datetime = Field(description="Only sales before this time are used, with timezone offset.")

    @field_validator("as_of")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must include a timezone offset")
        return value


class DemandRow(BaseModel):
    variation_id: str
    modifiers: list[str]
    label: str
    category: str
    normal: int
    extra_low: int
    extra_typical: int
    extra_high: int


class DemandExample(BaseModel):
    event_id: str
    name: str
    date: date
    extra_typical_total: int = Field(description="Extra items this past event implies for the upcoming one.")


class Scenario(BaseModel):
    extra_drinks: int
    extra_food: int
    items: list[SaleItemV2Create] = Field(description="Extra demand, ready for calculate_shortfall.items.")


class TotalsRow(BaseModel):
    normal: int
    extra_low: int
    extra_typical: int
    extra_high: int


class EventDemandResult(BaseModel):
    cafe_id: str
    event_id: str
    event_name: str
    event_type: str
    starts_at: datetime
    ends_at: datetime
    as_of: datetime
    confidence: Literal["good", "rough", "none"]
    examples: list[DemandExample]
    normal_rule: str
    normal_dates: list[date]
    rows: list[DemandRow]
    drinks: TotalsRow
    food: TotalsRow
    scenarios: dict[str, Scenario]
    notes: list[str]


def _label(names: dict[str, tuple[str, str, str]], key: Key) -> tuple[str, str]:
    variation_id, modifiers = key
    product_id, product_name, variation_name = names.get(variation_id, (variation_id, variation_id, ""))
    size = variation_name.split(" ")[0] if variation_name else ""
    extras = [m.rsplit("MILK_", 1)[-1].lower() + " milk" if "MILK_" in m else m for m in modifiers]
    label = f"{product_name}{f' ({size})' if size and size != 'Regular' else ''}"
    if extras:
        label += ", " + ", ".join(extras)
    category = "food" if "BAK" in product_id else "drink"
    return label, category


def _apportion(values: dict[Key, float]) -> dict[Key, int]:
    """Round each value so the rounded parts still add up to the rounded total."""
    if not values:
        return {}
    target = round(sum(values.values()))
    floors = {k: int(v // 1) for k, v in values.items()}
    remainder = target - sum(floors.values())
    order = sorted(values, key=lambda k: values[k] - floors[k], reverse=remainder > 0)
    step = 1 if remainder > 0 else -1
    for key in order[: abs(remainder)]:
        floors[key] += step
    return floors


def _hours(event: CafeEvent, tz: ZoneInfo) -> tuple[date, int, int]:
    start, end = event.starts_at.astimezone(tz), event.ends_at.astimezone(tz)
    end_hour = end.hour + (1 if end.minute else 0)
    return start.date(), start.hour, end_hour


def estimate_event_demand(session: Session, params: EventDemandInput) -> EventDemandResult:
    event = session.get(CafeEvent, params.event_id)
    if event is None or event.cafe_id != params.cafe_id:
        raise LookupError(f"event not found: {params.event_id}")
    history = load_history(session, params.cafe_id, params.as_of)
    tz = history.tz
    day, start_hour, end_hour = _hours(event, tz)
    weekday_name = day.strftime("%A")

    normal, normal_dates = history.normal(day, start_hour, end_hour)
    notes: list[str] = []
    if not normal_dates:
        notes.append(f"No sales history for normal {weekday_name}s yet.")

    same_type = list(
        session.scalars(
            select(CafeEvent)
            .where(
                CafeEvent.cafe_id == params.cafe_id,
                CafeEvent.event_type == event.event_type,
                CafeEvent.status == "happened",
                CafeEvent.ends_at <= params.as_of,
                CafeEvent.event_id != event.event_id,
            )
            .order_by(CafeEvent.starts_at)
        )
    )
    # Prefer past editions of the same event (last Halloween), then the same type.
    same_name = [e for e in same_type if e.name == event.name]
    past = same_name or same_type

    def group(key: Key) -> tuple[str, tuple[str, ...]]:
        milks = tuple(sorted({"oat" if "MILK_OAT" in m else "other_milk" for m in key[1]}))
        return history.categories.get(key[0], "other"), milks

    # one estimate of the upcoming extra demand per past event
    estimates: list[dict[Key, float]] = []
    ratios_by_group: dict[tuple[str, tuple[str, ...]], list[float]] = defaultdict(list)
    examples: list[DemandExample] = []
    for other in past:
        other_day, other_start, other_end = _hours(other, tz)
        if other_day not in history.by_day:
            continue
        actual = history.window(other_day, other_start, other_end)
        expected, dates = history.normal(other_day, other_start, other_end)
        if not dates:
            continue
        actual_by_group: dict = defaultdict(float)
        expected_by_group: dict = defaultdict(float)
        for key, qty in actual.items():
            actual_by_group[group(key)] += qty
        for key, qty in expected.items():
            expected_by_group[group(key)] += qty
        ratio = {
            g: (actual_by_group[g] + SMOOTHING) / (expected_by_group[g] + SMOOTHING)
            for g in set(actual_by_group) | set(expected_by_group)
        }
        estimate = {key: qty * (ratio.get(group(key), 1.0) - 1) for key, qty in normal.items()}
        estimates.append(estimate)
        for g in {group(key) for key in normal}:
            ratios_by_group[g].append(ratio.get(g, 1.0))
        examples.append(
            DemandExample(
                event_id=other.event_id,
                name=other.name,
                date=other_day,
                extra_typical_total=round(sum(estimate.values())),
            )
        )

    count = len(estimates)
    confidence: Literal["good", "rough", "none"] = "good" if count >= 3 else ("rough" if count else "none")
    kind = "editions of this event" if same_name else f"{event.event_type.replace('_', ' ')} events"
    if confidence == "rough":
        noun = kind if count > 1 else kind.replace("editions", "edition").replace("events", "event")
        notes.append(f"Only {count} past {noun}: treat as a rough guide.")
    if confidence == "none":
        notes.append(f"No past {event.event_type.replace('_', ' ')} events in the history: showing a normal day only.")

    # Low / typical / high: for each product group (menu category x milk choice) take the
    # low end, middle and high end of the effects seen across past events. Every ingredient then moves
    # in the same direction from low to high.
    labels = {key: _label(history.names, key) for key in normal}

    def scenario_ratio(g: tuple[str, tuple[str, ...]], name: str) -> float:
        values = sorted(ratios_by_group.get(g, []))
        if not values:
            return 1.0
        n = len(values)
        # middle range: about the 20th and 80th percentile once there are enough examples
        low_i, high_i = (round(0.2 * (n - 1)), round(0.8 * (n - 1))) if n >= 5 else (0, n - 1)
        return {"low": values[low_i], "typical": values[(n - 1) // 2], "high": values[high_i]}[name]

    chosen = {
        name: {key: qty * (scenario_ratio(group(key), name) - 1) for key, qty in normal.items()}
        for name in ("low", "typical", "high")
    }
    keys_by_category: dict[str, list[Key]] = defaultdict(list)
    for key, (_, category) in labels.items():
        keys_by_category[category].append(key)
    rounded: dict[str, dict[Key, int]] = {"normal": {}, "low": {}, "typical": {}, "high": {}}
    for category, keys in keys_by_category.items():
        rounded["normal"].update(_apportion({k: normal.get(k, 0.0) for k in keys}))
        for name in ("low", "typical", "high"):
            rounded[name].update(_apportion({k: chosen[name].get(k, 0.0) for k in keys}))

    rows: list[DemandRow] = []
    for key, (label, category) in labels.items():
        base, low, typical, high = (rounded[n].get(key, 0) for n in ("normal", "low", "typical", "high"))
        if base == 0 and low == typical == high == 0:
            continue
        rows.append(
            DemandRow(
                variation_id=key[0], modifiers=list(key[1]), label=label, category=category,
                normal=base, extra_low=low, extra_typical=typical, extra_high=high,
            )
        )
    rows.sort(key=lambda r: (-r.extra_typical, -r.normal, r.label))

    def totals(category: str) -> TotalsRow:
        subset = [r for r in rows if r.category == category]
        return TotalsRow(
            normal=sum(r.normal for r in subset),
            extra_low=sum(r.extra_low for r in subset),
            extra_typical=sum(r.extra_typical for r in subset),
            extra_high=sum(r.extra_high for r in subset),
        )

    def scenario(field: str) -> Scenario:
        items = [
            SaleItemV2Create(
                variation_id=r.variation_id,
                quantity=getattr(r, field),
                modifiers=[{"modifier_id": m, "quantity": 1} for m in r.modifiers],
            )
            for r in rows
            if getattr(r, field) > 0
        ]
        return Scenario(
            extra_drinks=sum(getattr(r, field) for r in rows if r.category == "drink"),
            extra_food=sum(getattr(r, field) for r in rows if r.category == "food"),
            items=items,
        )

    return EventDemandResult(
        cafe_id=params.cafe_id,
        event_id=event.event_id,
        event_name=event.name,
        event_type=event.event_type,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        as_of=params.as_of,
        confidence=confidence,
        examples=examples,
        normal_rule=(
            f"average of the last {len(normal_dates)} {weekday_name}s without events, "
            f"{time(start_hour).strftime('%-I %p')}–{time(end_hour % 24).strftime('%-I %p')}"
        ),
        normal_dates=normal_dates,
        rows=rows,
        drinks=totals("drink"),
        food=totals("food"),
        scenarios={name: scenario(f"extra_{name}") for name in ("low", "typical", "high")},
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Normal use between two times (for stock at event time)
# ---------------------------------------------------------------------------


def normal_items_between(
    session: Session, cafe_id: str, start: datetime, end: datetime, opening_hours: dict[int, tuple[int, int]]
) -> tuple[list[SaleItemV2Create], list[str]]:
    """Expected normal sales from ``start`` to ``end`` (open hours only), as items."""
    history = load_history(session, cafe_id, start)
    tz = history.tz
    local_start, local_end = start.astimezone(tz), end.astimezone(tz)
    totals: dict[Key, float] = defaultdict(float)
    notes: list[str] = []
    day = local_start.date()
    while day <= local_end.date():
        open_hour, close_hour = opening_hours.get(day.weekday(), (7, 19))
        first = max(open_hour, local_start.hour + (1 if local_start.minute else 0)) if day == local_start.date() else open_hour
        last = min(close_hour, local_end.hour + (1 if local_end.minute else 0)) if day == local_end.date() else close_hour
        if first < last:
            normal, dates = history.normal(day, first, last)
            if not dates:
                notes.append(f"No history for {day.strftime('%A')}s; normal use on {day} not counted.")
            for key, qty in normal.items():
                totals[key] += qty
        day += timedelta(days=1)
    items = [
        SaleItemV2Create(
            variation_id=key[0],
            quantity=round(qty),
            modifiers=[{"modifier_id": m, "quantity": 1} for m in key[1]],
        )
        for key, qty in sorted(totals.items())
        if round(qty) > 0
    ]
    return items, notes
