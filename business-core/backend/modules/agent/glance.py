"""At a glance (Plan home): what's running low, what's coming in, who's working, read from the records.

Every number is a lookup result formatted by code: stock estimates against reorder points, open orders
and regular deliveries, and scheduled shifts. Nothing is estimated or invented here.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.cafe import Cafe
from backend.modules.agent.summaries import clock, day, quantity, span
from backend.modules.inventory.models import InventoryItem
from backend.modules.inventory.service import InventoryService
from backend.modules.purchasing.service import upcoming_deliveries
from backend.modules.staffing.service import StaffingService

DELIVERY_DAYS = 3
MAX_ROWS = 4


def _row(name: str, text: str, detail: str | None = None) -> dict[str, Any]:
    return {"name": name, "text": text, "detail": detail}


def glance(session: Session, cafe_id: str, as_of: datetime) -> dict[str, Any]:
    cafe = session.get(Cafe, cafe_id)
    if cafe is None:
        raise LookupError(cafe_id)
    tz = ZoneInfo(cafe.timezone)
    now = as_of.astimezone(tz)

    points = dict(session.execute(select(InventoryItem.ingredient_id, InventoryItem.reorder_point)
                                  .where(InventoryItem.cafe_id == cafe_id, InventoryItem.reorder_point.is_not(None))).all())
    low = [
        _row(s.name, f"{quantity(s.estimated_quantity, s.base_unit)} left",
             f"reorder at {quantity(points[s.ingredient_id], s.base_unit)}")
        for s in InventoryService(session).list_stock_estimates(cafe_id=cafe_id, as_of=now)
        if s.stock_status == "known" and s.ingredient_id in points and s.estimated_quantity < points[s.ingredient_id]
    ]

    coming = []
    arriving = upcoming_deliveries(session, cafe_id, now, now + timedelta(days=DELIVERY_DAYS))
    # Open supplier orders first (they're what the owner is waiting on), then the regular deliveries.
    arriving = [d for d in arriving if d["kind"] == "order"] + [d for d in arriving if d["kind"] != "order"]
    for d in arriving[:MAX_ROWS]:
        source = (f"{d['supplier']} {d['po_number'] or ''}".strip() + f", {d['status']}") if d["kind"] == "order" else "regular delivery"
        coming.append(_row(d["name"], quantity(d["quantity"], d["unit"]), f"{day(d['arrives_at'])}, {clock(d['arrives_at'])} · {source}"))

    # Who's on today; after closing time, the next day anyone works (looked up, never assumed).
    staffing = StaffingService(session)
    working: list[dict[str, Any]] = []
    working_label, working_day = "Working today", day(now)
    for offset in range(8):
        start = datetime.combine((now + timedelta(days=offset)).date(), datetime.min.time(), tz)
        shifts = [s for s in staffing.list_shifts(cafe_id, max(start, now) if offset == 0 else start, start + timedelta(days=1))
                  if s.status == "scheduled"]
        if shifts:
            shifts.sort(key=lambda s: (s.start_at, s.employee_name))
            working = [_row(s.employee_name, span(s.start_at.isoformat(), s.end_at.isoformat(), tz, with_day=False))
                       for s in shifts]
            working_label = "Working today" if offset == 0 else ("Working tomorrow" if offset == 1 else "Working next")
            working_day = day(start)
            break
    return {"as_of": now.isoformat(), "running_low": low, "coming_in": coming, "working": working,
            "working_label": working_label, "working_day": working_day}
