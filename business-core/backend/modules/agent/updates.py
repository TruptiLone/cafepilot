"""Status changes for the Updates bell, read from records that already exist.

Events Muse found (the demo's scripted discovery), supplier orders (sent, confirmed, rejected, delivered) come from purchase_orders and their checked-in
receipts; shift replies (confirmed,
declined) from shift_offers; replans from the replan's run (finished) and its ai_calls row (started,
when the request began). Nothing is invented: every line is built from one of those records, and
"simulated" is the record's own flag (the reply came from the demo policy).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.cafe import Cafe
from backend.modules.agent.models import AICall, AgentPlanRun
from backend.modules.agent.summaries import clock, day, quantity, span
from backend.modules.events.models import CafeEvent
from backend.modules.inventory.models import InventoryItem
from backend.modules.purchasing.models import PurchaseOrder, ShiftOffer
from backend.modules.purchasing.service import delivery_for
from backend.modules.staffing.models import Employee, Shift

CONFIRMED_ORDERS = ("confirmed", "partially_received", "received")


def _items(order: PurchaseOrder, names: dict[str, str]) -> str:
    parts = []
    for line in order.lines:
        name = names.get(line.ingredient_id, line.ingredient_id.replace("_", " ").title()).lower()
        if line.packs and line.pack_name:  # "1 case oat milk (6 × 1 L)", like the chat says it
            unit, _, contents = line.pack_name.partition(" of ")
            parts.append(f"{line.packs} {unit}{'s' if line.packs > 1 else ''} {name}" + (f" ({contents})" if contents else ""))
        else:
            parts.append(f"{quantity(line.quantity, line.unit)} {name}")
    return ", ".join(parts)


def updates(session: Session, cafe_id: str, since: datetime | None = None, limit: int = 50) -> list[dict[str, Any]]:
    cafe = session.get(Cafe, cafe_id)
    if cafe is None:
        raise LookupError(cafe_id)
    tz = ZoneInfo(cafe.timezone)
    ingredients = dict(session.execute(select(InventoryItem.ingredient_id, InventoryItem.name)
                                       .where(InventoryItem.cafe_id == cafe_id)).all())
    people = dict(session.execute(select(Employee.employee_id, Employee.name).where(Employee.cafe_id == cafe_id)).all())
    out: list[dict[str, Any]] = []

    def add(key: str, kind: str, group: str, at: datetime | None, title: str, detail: str | None,
            simulated: bool, run_id: str | None, model: str | None = None) -> None:
        if at is None or (since is not None and at <= since):
            return
        out.append({"id": key, "kind": kind, "group": group, "at": at, "title": title,
                    "detail": detail or None, "simulated": simulated, "run_id": run_id, "model": model})

    for order in session.scalars(select(PurchaseOrder).where(PurchaseOrder.cafe_id == cafe_id)):
        po = order.po_number or order.order_id
        items = _items(order, ingredients)
        add(f"order:{order.order_id}:sent", "order_sent", "orders", order.sent_at,
            f"Order {po} sent to {order.supplier_name}", items, order.simulated, order.source_run_id)
        if order.status in CONFIRMED_ORDERS:
            arrives = (order.confirmed_delivery_at or order.requested_delivery_at).astimezone(tz)
            add(f"order:{order.order_id}:confirmed", "order_confirmed", "orders", order.responded_at,
                f"{order.supplier_name} confirmed {po}", f"{items}; arriving {day(arrives)} {clock(arrives)}",
                order.simulated, order.source_run_id)
            if order.status in ("received", "partially_received"):
                receipt = delivery_for(session, order.order_id)
                fully = order.status == "received"
                add(f"order:{order.order_id}:delivered", "order_delivered" if fully else "order_partly_delivered", "orders",
                    receipt.confirmed_at if receipt else None,
                    f"{po} {'delivered' if fully else 'partly delivered'}: {order.supplier_name}",
                    f"Checked in{f' (slip {receipt.slip_number})' if receipt and receipt.slip_number else ''}"
                    + ("; everything matched" if fully else "; a credit request covers what's missing"),
                    False, order.source_run_id)
        elif order.status == "rejected":
            add(f"order:{order.order_id}:rejected", "order_rejected", "orders", order.responded_at,
                f"{order.supplier_name} declined {po}", order.response_reason, order.simulated, order.source_run_id)

    offers = session.scalars(select(ShiftOffer).where(ShiftOffer.cafe_id == cafe_id,
                                                      ShiftOffer.status.in_(("accepted", "declined"))))
    for offer in offers:
        shift = session.get(Shift, offer.shift_id)
        window = span(shift.start_at.isoformat(), shift.end_at.isoformat(), tz) if shift else ""
        who = people.get(offer.employee_id, offer.employee_id)
        verb = "confirmed" if offer.status == "accepted" else "declined"
        add(f"shift:{offer.shift_id}:{offer.status}", f"shift_{verb}", "staff", offer.responded_at,
            f"{who} {verb} {window}".strip(), offer.response_note, offer.simulated, offer.source_run_id)

    replans = session.scalars(select(AgentPlanRun).where(AgentPlanRun.cafe_id == cafe_id,
                                                         AgentPlanRun.parent_run_id.is_not(None)))
    for run in replans:
        call = session.scalar(select(AICall).where(AICall.run_id == run.run_id))
        if call is not None:  # the request began latency_ms before the row was written
            add(f"replan:{run.run_id}:started", "replan_started", "plans",
                call.created_at - timedelta(milliseconds=call.latency_ms),
                "Muse started looking for another way", None, False, run.parent_run_id, call.model)
        add(f"replan:{run.run_id}:finished", "replan_finished", "plans", run.created_at,
            "Muse found another way", run.goal, False, run.run_id, run.model)

    # Demo only: the scripted event discovery (see events.DEMO_DISCOVERY_EVENT_IDS), so simulated.
    found = session.scalars(select(CafeEvent).where(CafeEvent.cafe_id == cafe_id, CafeEvent.discovered_at.is_not(None)))
    for event in found:
        starts = event.starts_at.astimezone(tz)
        add(f"event:{event.event_id}:found", "event_found", "plans", event.discovered_at,
            f"Muse found a new event: {event.name}, {day(starts)}",
            "; ".join(p for p in (event.location, event.notes) if p) or None, event.is_synthetic, None)

    out.sort(key=lambda u: u["at"], reverse=True)  # real instants, not strings (offsets differ across DST)
    return [{**u, "at": u["at"].astimezone(tz).isoformat()} for u in out[:limit]]
