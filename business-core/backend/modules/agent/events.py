"""Events Muse has detected near the café: the trigger that starts a plan.

Events come from the ``cafe_events`` calendar. For each upcoming event, Muse's opening line and
the expected demand are computed from sales history (``estimate_event_demand``), never typed in.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.config import get_settings
from backend.models.cafe import Cafe
from backend.modules.agent.cafe_policies import event_required_staff
from backend.modules.agent.demand import EventDemandInput, EventDemandResult, estimate_event_demand
from backend.modules.agent.summaries import clock, day
from backend.modules.events.models import CafeEvent
from backend.modules.staffing.models import Employee
from backend.modules.staffing.service import StaffingService


class ExpectedDemand(BaseModel):
    confidence: str = Field(description="good (3+ past examples), rough (1-2), none (0).")
    examples: int
    normal_drinks: int
    extra_drinks_low: int
    extra_drinks_typical: int
    extra_drinks_high: int
    extra_food_typical: int
    basis: str = Field(description="One plain sentence on where the numbers come from.")


class DetectedEvent(BaseModel):
    event_id: str
    cafe_id: str
    title: str
    category: str
    starts_at: datetime
    ends_at: datetime
    location: str | None
    distance_m: int | None
    source: str
    is_demo: bool = Field(description="True when the event and the history behind it are demo data.")
    detected_at: datetime | None
    knowledge_as_of: datetime = Field(description="Stock, sales and staffing are checked as of this time.")
    expected: ExpectedDemand
    muse_message: str = Field(description="Muse's opening line, written by code from the history.")
    request: str = Field(description="The request sent to the planner when the owner says yes.")
    run_id: str | None = Field(default=None, description="Muse's plan for this event, if one was started.")
    latest_run_id: str | None = Field(default=None, description="The newest plan in that family (after replans).")
    plan_state: str = Field(default="new", description="new, checking, needs_ok, waiting, planned or issues.")
    planner_model: str = Field(
        description="The model that will plan this event (configured, e.g. muse-wonder-1.0), so the UI can "
        "show it while the plan is on its way. A finished plan records the model that actually ran."
    )


def _signed(n: int) -> str:
    return f"+{n}" if n > 0 else str(n)


def _describe(event: CafeEvent, demand: EventDemandResult, tz: ZoneInfo) -> tuple[ExpectedDemand, str, str]:
    start, end = event.starts_at.astimezone(tz), event.ends_at.astimezone(tz)
    when = f"{day(start)}, {clock(start)}–{clock(end)}"
    where = f", {event.distance_m} m away" if event.distance_m else ""
    d = demand.drinks
    n = len(demand.examples)
    same_event = n > 0 and all(e.name == event.name for e in demand.examples)
    if same_event:
        examples = "last year's edition" if n == 1 else f"the last {n} editions"
    else:
        kind = event.event_type.replace("_", " ")
        examples = f"{n} past {kind} event{'' if n == 1 else 's'}"
    if demand.confidence == "none":
        basis = f"No past {event.event_type.replace('_', ' ')} events in the sales history."
        effect = "I have no past events like this to learn from, so I can only show a normal day."
    else:
        if d.extra_low == d.extra_high:
            range_text = f"{_signed(d.extra_typical)} drinks"
        else:
            range_text = f"{_signed(d.extra_low)} to {_signed(d.extra_high)} extra drinks, typically {_signed(d.extra_typical)}"
        basis = f"Compared {examples} with normal {start.strftime('%A')}s ({demand.normal_rule})."
        subject = f"{examples[0].upper()}{examples[1:]}"
        if d.extra_high < 0:
            effect = f"{subject} was quieter: about {abs(d.extra_typical)} fewer drinks than a normal {d.normal}."
        elif d.extra_typical == 0 and d.extra_low <= 0:
            effect = f"{subject} made little difference to a normal {d.normal} drinks."
        else:
            effect = f"{subject} brought {range_text} on top of a normal {d.normal}."
        if demand.confidence == "rough":
            effect += " That's only a rough guide."
    expected = ExpectedDemand(
        confidence=demand.confidence,
        examples=n,
        normal_drinks=d.normal,
        extra_drinks_low=d.extra_low,
        extra_drinks_typical=d.extra_typical,
        extra_drinks_high=d.extra_high,
        extra_food_typical=demand.food.extra_typical,
        basis=basis,
    )
    message = f"{event.name}: {when}{where}. {effect} Want me to check if we're ready?"
    request = (
        f"Upcoming event {event.event_id}: {event.name}, {when}. Use estimate_event_demand for this event "
        f"and the typical scenario, with knowledge as of {demand.as_of.astimezone(tz).isoformat()}. Order milk for a 6 AM "
        f"delivery on the event day if we'll run short (count normal use and regular deliveries), and add "
        f"baristas for any event hours below our event staffing target."
    )
    return expected, message, request


def _to_detected(session: Session, event: CafeEvent, tz: ZoneInfo, as_of: datetime) -> DetectedEvent:
    demand = estimate_event_demand(
        session, EventDemandInput(cafe_id=event.cafe_id, event_id=event.event_id, as_of=as_of)
    )
    expected, message, request = _describe(event, demand, tz)
    return DetectedEvent(
        event_id=event.event_id,
        cafe_id=event.cafe_id,
        title=event.name,
        category=event.event_type,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        location=event.location,
        distance_m=event.distance_m,
        source=event.source,
        is_demo=event.is_synthetic,
        detected_at=event.detected_at,
        knowledge_as_of=as_of,
        expected=expected,
        muse_message=message,
        request=request,
        planner_model=get_settings().planner_model,
    )


def _knowledge_as_of(event: CafeEvent) -> datetime:
    # Demo events are pinned to when they were detected so the demo replays identically.
    return event.detected_at or datetime.now(event.starts_at.tzinfo)


def detected_events(session: Session, cafe_id: str) -> list[DetectedEvent]:
    cafe = session.get(Cafe, cafe_id)
    if cafe is None:
        return []
    tz = ZoneInfo(cafe.timezone)
    rows = session.scalars(
        select(CafeEvent)
        .where(CafeEvent.cafe_id == cafe_id, CafeEvent.status == "upcoming")
        .order_by(CafeEvent.starts_at)
    )
    return [_to_detected(session, event, tz, _knowledge_as_of(event)) for event in rows]


def get_event(session: Session, event_id: str) -> DetectedEvent | None:
    event = session.get(CafeEvent, event_id)
    if event is None or event.status != "upcoming":
        return None
    cafe = session.get(Cafe, event.cafe_id)
    tz = ZoneInfo(cafe.timezone if cafe else "UTC")
    return _to_detected(session, event, tz, _knowledge_as_of(event))


def upcoming_for_planner(session: Session, cafe_id: str, as_of: datetime) -> list[dict]:
    """Compact list for the planner's reference data."""
    rows = session.scalars(
        select(CafeEvent)
        .where(CafeEvent.cafe_id == cafe_id, CafeEvent.status == "upcoming", CafeEvent.ends_at >= as_of)
        .order_by(CafeEvent.starts_at)
        .limit(10)
    )
    return [
        {
            "event_id": e.event_id,
            "name": e.name,
            "event_type": e.event_type,
            "starts_at": e.starts_at.isoformat(),
            "ends_at": e.ends_at.isoformat(),
        }
        for e in rows
    ]


def event_staffing(session: Session, cafe_id: str, event_id: str) -> dict[str, Any] | None:
    """Staffing for an event, worked out in code for the planner: required vs scheduled people per
    window of the event hours (the coverage_report code, consecutive hours with the same count
    merged) and that day's roster. The model reads these numbers; it never recalculates them."""
    event = session.get(CafeEvent, event_id)
    cafe = session.get(Cafe, event.cafe_id) if event else None
    if event is None or cafe is None or event.cafe_id != cafe_id:
        return None
    tz = ZoneInfo(cafe.timezone)
    required, basis = event_required_staff(event.cafe_id, event.event_type)
    service = StaffingService(session)
    windows: list[dict[str, Any]] = []
    for hour in service.coverage_report(event.cafe_id, event.starts_at, event.ends_at, required).hours:
        start = hour.hour_start.astimezone(tz)
        end = min(start + timedelta(hours=1), event.ends_at.astimezone(tz))
        if windows and windows[-1]["scheduled"] == hour.scheduled_employees:
            windows[-1]["end_at"] = end.isoformat()
            continue
        windows.append({
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "scheduled": hour.scheduled_employees,
            "required": required,
            "short_by": max(required - hour.scheduled_employees, 0),
        })
    day = event.starts_at.astimezone(tz).date()
    day_start = datetime.combine(day, time.min, tz)
    day_end = datetime.combine(day + timedelta(days=1), time.min, tz)
    roles = dict(session.execute(select(Employee.employee_id, Employee.role).where(Employee.cafe_id == event.cafe_id)).all())
    roster = [
        {"employee_id": s.employee_id, "name": s.employee_name, "role": roles.get(s.employee_id),
         "start_at": s.start_at.astimezone(tz).isoformat(), "end_at": s.end_at.astimezone(tz).isoformat()}
        for s in service.list_shifts(event.cafe_id, day_start, day_end)
        if s.status == "scheduled"
    ]
    return {
        "event_id": event.event_id,
        "event_hours": {"start_at": event.starts_at.astimezone(tz).isoformat(), "end_at": event.ends_at.astimezone(tz).isoformat()},
        "required_staff": required,
        "required_from": f"cafe_policies.staffing.{basis}",
        "coverage_by_window": windows,
        "short_windows": [w for w in windows if w["short_by"] > 0],
        "roster": roster,
        "note": "Worked out in code from the schedule (coverage_report and list_shifts). Use these numbers as given.",
    }
