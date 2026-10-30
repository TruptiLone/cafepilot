"""Café status: the read-only panel beside Muse (stock for the next event, staff cover, approvals, sales).

Everything here is computed from the café's data with the same code the plans use
(estimate_event_demand + calculate_shortfall), so the panel and Muse never disagree.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.cafe import Cafe
from backend.modules.agent.acknowledgements import change_statuses
from backend.modules.agent.calculations import IncompleteRecipeError, ShortfallInput, calculate_shortfall
from backend.modules.agent.cafe_policies import opening_hours, policies_for
from backend.modules.agent.demand import EventDemandInput, EventDemandResult, estimate_event_demand
from backend.modules.agent.models import AgentNodeRun, AgentPlanRun
from backend.modules.events.models import CafeEvent
from backend.modules.inventory.service import InventoryService
from backend.modules.purchasing.models import PurchaseOrder
from backend.modules.sales.service import SalesService
from backend.modules.staffing.service import StaffingService

PlanState = Literal["new", "checking", "needs_ok", "waiting", "planned", "issues"]


class Kpi(BaseModel):
    label: str
    value: str
    of: str | None = None
    note: str
    tone: Literal["neutral", "good", "warn", "bad"] = "neutral"


class InventoryRow(BaseModel):
    ingredient_id: str
    name: str
    unit: str
    on_hand: Decimal | None
    arriving: Decimal
    on_order: Decimal
    on_order_status: str | None
    needed: Decimal
    shortfall: Decimal
    status: Literal["covered", "short", "ordered", "unknown"]
    note: str


class HalfDay(BaseModel):
    label: Literal["AM", "PM"]
    staffed: int
    required: int


class CoverageDay(BaseModel):
    date: date
    label: str
    shifts: int
    is_event_day: bool
    halves: list[HalfDay]


class EventStatus(BaseModel):
    event_id: str
    name: str
    starts_at: datetime
    ends_at: datetime
    note: str
    plan_state: PlanState
    run_id: str | None
    latest_run_id: str | None


class CafeStatus(BaseModel):
    cafe_id: str
    cafe_name: str
    as_of: datetime
    greeting: str
    date_label: str
    focus_event_id: str | None
    focus_event_name: str | None
    kpis: dict[str, Kpi]
    inventory: list[InventoryRow]
    coverage: list[CoverageDay]
    events: list[EventStatus]


EVENT_PREFIX = "Upcoming event "


def _q(value: Decimal | None, unit: str) -> str:
    if value is None:
        return "unknown"
    if unit == "mL":
        litres = value / 1000
        return f"{litres.quantize(Decimal('0.1')).normalize():f} L"
    if unit == "g":
        kg = value / 1000
        return f"{kg.quantize(Decimal('0.1')).normalize():f} kg"
    return f"{value.quantize(Decimal('1')):f}"


def status_as_of(session: Session, cafe_id: str) -> datetime:
    """Demo events are pinned to when Muse detected them so the demo replays identically."""
    detected = session.scalar(
        select(func.max(CafeEvent.detected_at)).where(CafeEvent.cafe_id == cafe_id, CafeEvent.status == "upcoming")
    )
    return detected or datetime.now(ZoneInfo("UTC"))


# ---------------------------------------------------------------------------
# Plans for events
# ---------------------------------------------------------------------------

def _family(session: Session, run: AgentPlanRun) -> list[AgentPlanRun]:
    """The run and every replan that came from it."""
    family, frontier = [run], [run.run_id]
    while frontier:
        children = list(session.scalars(select(AgentPlanRun).where(AgentPlanRun.parent_run_id.in_(frontier))))
        family += children
        frontier = [c.run_id for c in children]
    return family


def _plan_state(session: Session, runs: list[AgentPlanRun]) -> PlanState:
    if any(r.status == "running" for r in runs):
        return "checking"
    if any(r.status == "awaiting_approval" for r in runs):
        return "needs_ok"
    latest = max(runs, key=lambda r: r.created_at)
    _, _, waiting = change_statuses(session, latest)
    if waiting:
        return "waiting"
    if latest.status == "completed_with_issues" and any(n.status == "failed" for n in latest.nodes):
        return "issues"
    return "planned"


def event_plan(session: Session, event_id: str) -> tuple[str | None, str | None, PlanState]:
    """(first run for the event, newest run in its family, state)."""
    run = session.scalar(
        select(AgentPlanRun)
        .where(AgentPlanRun.query.startswith(f"{EVENT_PREFIX}{event_id}:"))
        .order_by(AgentPlanRun.created_at.desc())
        .limit(1)
    )
    if run is None:
        return None, None, "new"
    family = _family(session, run)
    latest = max(family, key=lambda r: r.created_at)
    return run.run_id, latest.run_id, _plan_state(session, family)


# ---------------------------------------------------------------------------
# Stock for the next busy event
# ---------------------------------------------------------------------------

def _on_order(session: Session, cafe_id: str, by: datetime) -> dict[str, tuple[Decimal, str]]:
    """Ingredient -> (quantity, best status) for open orders arriving by ``by`` (not yet received)."""
    rank = {"sent": 1, "confirmed": 2}
    out: dict[str, tuple[Decimal, str]] = {}
    orders = session.scalars(
        select(PurchaseOrder).where(
            PurchaseOrder.cafe_id == cafe_id,
            PurchaseOrder.status.in_(("sent", "confirmed")),
            PurchaseOrder.requested_delivery_at <= by,
        )
    )
    for order in orders:
        for line in order.lines:
            qty, state = out.get(line.ingredient_id, (Decimal(0), "sent"))
            best = order.status if rank[order.status] >= rank.get(state, 0) or qty == 0 else state
            out[line.ingredient_id] = (qty + line.quantity, best)
    return out


def _inventory(session: Session, cafe_id: str, event: CafeEvent, demand: EventDemandResult, as_of: datetime,
               tz: ZoneInfo) -> list[InventoryRow]:
    try:
        result = calculate_shortfall(
            session,
            ShortfallInput(
                cafe_id=cafe_id, as_of=as_of, target_at=event.starts_at,
                items=demand.scenarios["typical"].items, include_normal_use=True,
            ),
        )
    except IncompleteRecipeError:
        return []
    day = event.starts_at.astimezone(tz).strftime("%a")
    orders = _on_order(session, cafe_id, result.cover_until or event.ends_at)
    rows: list[InventoryRow] = []
    for line in result.lines:
        total_need = line.needed_with_buffer + line.normal_use
        if total_need <= 0:
            continue
        on_order, order_status = orders.get(line.ingredient_id, (Decimal(0), None))
        short = max(line.shortfall or Decimal(0), Decimal(0))
        if line.on_hand is None:
            status, note = "unknown", "No recent count"
        elif short <= 0:
            status, note = "covered", f"Needs {_q(total_need, line.unit)} through {day}"
        elif on_order >= short:
            status = "ordered"
            note = f"{_q(on_order, line.unit)} ordered · {'confirmed' if order_status == 'confirmed' else 'not confirmed yet'}"
        else:
            status, note = "short", f"Needs {_q(total_need, line.unit)} through {day}"
        if line.deliveries > 0 and status in ("covered", "short"):
            note += f" · {_q(line.deliveries, line.unit)} arriving"
        rows.append(InventoryRow(
            ingredient_id=line.ingredient_id, name=line.name, unit=line.unit, on_hand=line.on_hand,
            arriving=line.deliveries, on_order=on_order, on_order_status=order_status, needed=total_need,
            shortfall=short, status=status, note=note,
        ))
    order = {"short": 0, "ordered": 1, "unknown": 2, "covered": 3}
    rows.sort(key=lambda r: (order[r.status], -(r.needed)))
    return rows[:6]


# ---------------------------------------------------------------------------
# Staff cover
# ---------------------------------------------------------------------------

def _min_staffed(session: Session, cafe_id: str, start: datetime, end: datetime) -> int:
    report = StaffingService(session).coverage_report(cafe_id, start, end, 1)
    return min((h.scheduled_employees for h in report.hours), default=0)


def _coverage(session: Session, cafe_id: str, as_of: datetime, tz: ZoneInfo, events: list[CafeEvent]) -> list[CoverageDay]:
    staffing = policies_for(cafe_id).get("staffing", {})
    normal = int(staffing.get("normal_required_staff", 2))
    busy = int(staffing.get("busy_event_required_staff", normal))
    busy_events = [e for e in events if e.event_type not in ("holiday",)]
    hours = opening_hours(cafe_id)
    days: list[CoverageDay] = []
    first = as_of.astimezone(tz).date() + timedelta(days=1)
    for offset in range(3):
        day = first + timedelta(days=offset)
        open_h, close_h = hours.get(day.weekday(), (7, 19))
        opens, closes = datetime.combine(day, time(open_h), tz), datetime.combine(day, time(close_h), tz)
        noon = datetime.combine(day, time(12), tz)
        shifts = StaffingService(session).list_shifts(cafe_id, opens, closes)
        halves = []
        event_day = False
        report = StaffingService(session).coverage_report(cafe_id, opens, closes, 1)
        for label, start, end in (("AM", opens, noon), ("PM", noon, closes)):
            # Show the hardest hour of the half: event hours need the event target.
            worst: tuple[int, int, int] | None = None
            for hour in report.hours:
                if not (start <= hour.hour_start < end):
                    continue
                busy_hour = any(e.starts_at <= hour.hour_start < e.ends_at for e in busy_events)
                event_day = event_day or busy_hour
                required = busy if busy_hour else normal
                gap = required - hour.scheduled_employees
                if worst is None or gap > worst[0]:
                    worst = (gap, hour.scheduled_employees, required)
            staffed, required = (worst[1], worst[2]) if worst else (0, normal)
            halves.append(HalfDay(label=label, staffed=staffed, required=required))
        days.append(CoverageDay(
            date=day, label=datetime.combine(day, time(12)).strftime("%a, %b %-d"),
            shifts=len([s for s in shifts if getattr(s, "status", "scheduled") == "scheduled"]),
            is_event_day=event_day, halves=halves,
        ))
    return days


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

def _sales_kpi(session: Session, cafe_id: str, as_of: datetime, tz: ZoneInfo) -> Kpi:
    local = as_of.astimezone(tz)
    start = datetime.combine(local.date(), time(0), tz)
    today = SalesService(session).report(cafe_id, start, as_of).total_revenue_cents
    last = SalesService(session).report(cafe_id, start - timedelta(days=7), as_of - timedelta(days=7)).total_revenue_cents
    note, tone = "No sales last week to compare", "neutral"
    if last:
        change = round((today - last) * 100 / last)
        note = f"{'+' if change >= 0 else ''}{change}% vs last {local.strftime('%a')}"
        tone = "good" if change >= 0 else "warn"
    return Kpi(label="Today's sales", value=f"${today / 100:,.0f}", note=note, tone=tone)


def cafe_status(session: Session, cafe_id: str) -> CafeStatus:
    cafe = session.get(Cafe, cafe_id)
    if cafe is None:
        raise LookupError(cafe_id)
    tz = ZoneInfo(cafe.timezone)
    as_of = status_as_of(session, cafe_id)
    local = as_of.astimezone(tz)
    events = list(session.scalars(
        select(CafeEvent)
        .where(CafeEvent.cafe_id == cafe_id, CafeEvent.status == "upcoming", CafeEvent.ends_at >= as_of)
        .order_by(CafeEvent.starts_at)
    ))

    # The next event that needs more than a normal day is the one the panel prepares for.
    focus, focus_demand, event_rows = None, None, []
    for event in events:
        demand = estimate_event_demand(session, EventDemandInput(cafe_id=cafe_id, event_id=event.event_id, as_of=as_of))
        extra = demand.drinks.extra_typical
        run_id, latest_id, state = event_plan(session, event.event_id)
        start, end = event.starts_at.astimezone(tz), event.ends_at.astimezone(tz)
        hours = f"{start.strftime('%-I %p').replace(':00', '')}–{end.strftime('%-I %p')}"
        if demand.confidence == "none":
            note = f"{hours} · no past events like this"
        elif extra > 0:
            note = f"{hours} · usually +{extra} drinks"
        elif extra < 0:
            note = f"{hours} · usually about {abs(extra)} fewer drinks"
        else:
            note = f"{hours} · usually a normal day"
        event_rows.append(EventStatus(
            event_id=event.event_id, name=event.name, starts_at=event.starts_at, ends_at=event.ends_at,
            note=note, plan_state=state, run_id=run_id, latest_run_id=latest_id,
        ))
        if focus is None and extra > 0:
            focus, focus_demand = event, demand

    inventory = _inventory(session, cafe_id, focus, focus_demand, as_of, tz) if focus and focus_demand else []
    coverage = _coverage(session, cafe_id, as_of, tz, [focus] if focus else [])

    short = [r for r in inventory if r.status == "short"]
    ordered = [r for r in inventory if r.status == "ordered"]
    kpis: dict[str, Kpi] = {}
    if focus is not None:
        day = focus.starts_at.astimezone(tz).strftime("%A")
        if short:
            kpis["stock"] = Kpi(label=f"Short for {day}", value=str(len(short)),
                                note=" & ".join(r.name.lower() for r in short[:2]).capitalize(), tone="warn")
        elif ordered:
            kpis["stock"] = Kpi(label=f"Short for {day}", value="0", note="Covered once orders arrive", tone="neutral")
        else:
            kpis["stock"] = Kpi(label=f"Short for {day}", value="0", note="Everything covered", tone="good")
        staffing = policies_for(cafe_id).get("staffing", {})
        target = int(staffing.get("busy_event_required_staff", 4))
        staffed = _min_staffed(session, cafe_id, focus.starts_at, focus.ends_at)
        gap = max(target - staffed, 0)
        kpis["coverage"] = Kpi(label=f"{day} event coverage", value=str(min(staffed, target)), of=f"/ {target}",
                               note=f"{gap} short" if gap else "Fully staffed", tone="warn" if gap else "good")
    waiting = session.scalar(
        select(func.count()).select_from(AgentNodeRun).join(AgentPlanRun, AgentPlanRun.run_id == AgentNodeRun.run_id)
        .where(AgentPlanRun.cafe_id == cafe_id, AgentNodeRun.status == "awaiting_approval")
    ) or 0
    kpis["approvals"] = Kpi(label="Pending approvals", value=str(waiting),
                            note="From Muse" if waiting else "Nothing waiting", tone="warn" if waiting else "neutral")
    kpis["sales"] = _sales_kpi(session, cafe_id, as_of, tz)

    part = "morning" if local.hour < 12 else "afternoon" if local.hour < 17 else "evening"
    return CafeStatus(
        cafe_id=cafe_id, cafe_name=cafe.name, as_of=as_of,
        greeting=f"Good {part}, {cafe.name}.", date_label=local.strftime("%A, %B %-d"),
        focus_event_id=focus.event_id if focus else None, focus_event_name=focus.name if focus else None,
        kpis=kpis, inventory=inventory, coverage=coverage, events=event_rows,
    )
