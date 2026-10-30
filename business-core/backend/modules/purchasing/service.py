"""Sending orders to (simulated) suppliers and offering shifts to (simulated) staff.

The simulator decides its answer when an order or offer is sent and reveals it after a short,
configurable delay (``settle_due``), so the owner sees "Sent, waiting for Bay Dairy" before
"Confirmed" or "Rejected". Every simulated record carries ``simulated=True``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.cafe import Cafe
from backend.modules.agent.cafe_policies import policies_for
from backend.modules.purchasing.models import PurchaseOrder, PurchaseOrderLine, ShiftOffer


class SupplierNotFoundError(LookupError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def suppliers(cafe_id: str) -> list[dict[str, Any]]:
    return policies_for(cafe_id).get("suppliers", [])


GENERIC_SUPPLIER: dict[str, Any] = {
    "supplier_id": "SIM_SUPPLIER",
    "name": "Simulated supplier",
    "respond_after_seconds": 8,
    "earliest_delivery": {d: "00:00" for d in range(7)},
    "items": None,  # carries everything, no pack pricing
}


def get_supplier(cafe_id: str, supplier_id: str | None) -> dict[str, Any]:
    configured = suppliers(cafe_id)
    if not configured and supplier_id in (None, "SIM_SUPPLIER"):
        return GENERIC_SUPPLIER
    supplier_id = supplier_id or policies_for(cafe_id).get("ordering", {}).get("default_supplier_id")
    for supplier in configured:
        if supplier["supplier_id"] == supplier_id:
            return supplier
    raise SupplierNotFoundError(f"unknown supplier: {supplier_id}")


def _tz(session: Session, cafe_id: str) -> ZoneInfo:
    cafe = session.get(Cafe, cafe_id)
    return ZoneInfo(cafe.timezone if cafe else "UTC")


def _clock(moment: datetime) -> str:
    return moment.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")


def delivery_problem(supplier: dict[str, Any], requested: datetime, tz: ZoneInfo) -> str | None:
    """Why this supplier can't deliver at the requested time, or None if it can."""
    local = requested.astimezone(tz)
    earliest = supplier.get("earliest_delivery", {}).get(local.weekday())
    if earliest is None:
        return f"{supplier['name']} doesn't deliver on {local.strftime('%A')}s."
    hour, minute = (int(x) for x in earliest.split(":"))
    if local.time() < time(hour, minute):
        first = datetime.combine(local.date(), time(hour, minute), tz)
        return (
            f"{supplier['name']} can't deliver on {local.strftime('%A')}s before {_clock(first)} "
            f"(requested {_clock(local)})."
        )
    return None


@dataclass
class OrderLineIn:
    ingredient_id: str
    quantity: Decimal
    unit: str


def send_order(
    session: Session,
    *,
    cafe_id: str,
    supplier_id: str | None,
    lines: list[OrderLineIn],
    requested_delivery_at: datetime,
    order_id: str,
    source_run_id: str | None = None,
    source_node_id: str | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> PurchaseOrder:
    supplier = get_supplier(cafe_id, supplier_id)
    tz = _tz(session, cafe_id)
    sent_at = now or _now()

    problem = delivery_problem(supplier, requested_delivery_at, tz)
    order_lines: list[PurchaseOrderLine] = []
    total = 0
    for number, line in enumerate(lines, start=1):
        catalog = supplier.get("items")
        if catalog is None:  # generic simulated supplier: carries everything as asked
            order_lines.append(
                PurchaseOrderLine(line_no=number, ingredient_id=line.ingredient_id, quantity=line.quantity, unit=line.unit)
            )
            continue
        item = catalog.get(line.ingredient_id)
        if item is None:
            problem = problem or f"{supplier['name']} doesn't carry {line.ingredient_id.replace('_', ' ').lower()}."
            order_lines.append(
                PurchaseOrderLine(line_no=number, ingredient_id=line.ingredient_id, quantity=line.quantity, unit=line.unit)
            )
            continue
        pack_size = Decimal(item["pack_size"])
        packs = int((line.quantity / pack_size).to_integral_value(rounding=ROUND_CEILING))
        total += packs * int(item["pack_price_cents"])
        order_lines.append(
            PurchaseOrderLine(
                line_no=number, ingredient_id=line.ingredient_id, quantity=pack_size * packs, unit=line.unit,
                pack_size=pack_size, pack_name=item["pack_name"], packs=packs,
                pack_price_cents=int(item["pack_price_cents"]),
            )
        )

    order = PurchaseOrder(
        order_id=order_id,
        cafe_id=cafe_id,
        supplier_id=supplier["supplier_id"],
        supplier_name=supplier["name"],
        status="sent",
        requested_delivery_at=requested_delivery_at,
        sent_at=sent_at,
        respond_at=sent_at + timedelta(seconds=int(supplier.get("respond_after_seconds", 8))),
        pending_decision="rejected" if problem else "confirmed",
        pending_reason=problem,
        total_cents=total,
        simulated=True,
        source_run_id=source_run_id,
        source_node_id=source_node_id,
        note=note,
        lines=order_lines,
    )
    session.add(order)
    session.flush()
    return order


def offer_shift(
    session: Session,
    *,
    cafe_id: str,
    shift_id: str,
    employee_id: str,
    source_run_id: str | None = None,
    source_node_id: str | None = None,
    now: datetime | None = None,
) -> ShiftOffer:
    replies = policies_for(cafe_id).get("staff_replies", {})
    offered_at = now or _now()
    decline = employee_id in replies.get("declines", [])
    offer = ShiftOffer(
        shift_id=shift_id,
        cafe_id=cafe_id,
        employee_id=employee_id,
        status="pending",
        offered_at=offered_at,
        respond_at=offered_at + timedelta(seconds=int(replies.get("respond_after_seconds", 10))),
        pending_decision="declined" if decline else "accepted",
        simulated=True,
        source_run_id=source_run_id,
        source_node_id=source_node_id,
    )
    session.add(offer)
    session.flush()
    return offer


def settle_due(session: Session, *, cafe_id: str | None = None, now: datetime | None = None) -> int:
    """Reveal simulated answers whose time has come. Declined shifts are taken off the roster."""
    from backend.modules.staffing.service import StaffingService

    moment = now or _now()
    changed = 0
    orders = select(PurchaseOrder).where(PurchaseOrder.status == "sent", PurchaseOrder.respond_at <= moment)
    offers = select(ShiftOffer).where(ShiftOffer.status == "pending", ShiftOffer.respond_at <= moment)
    if cafe_id is not None:
        orders = orders.where(PurchaseOrder.cafe_id == cafe_id)
        offers = offers.where(ShiftOffer.cafe_id == cafe_id)
    for order in session.scalars(orders.with_for_update(skip_locked=True)):
        order.status = order.pending_decision or "confirmed"
        order.response_reason = order.pending_reason
        order.responded_at = order.respond_at
        if order.status == "confirmed":
            order.confirmed_delivery_at = order.requested_delivery_at
        changed += 1
    for offer in session.scalars(offers.with_for_update(skip_locked=True)):
        offer.status = offer.pending_decision or "accepted"
        offer.responded_at = offer.respond_at
        if offer.status == "declined":
            offer.response_note = "Declined the shift; it was taken off the schedule."
            try:
                StaffingService(session).cancel_shift(offer.shift_id, offer.cafe_id)
            except Exception:
                pass
        changed += 1
    if changed:
        session.flush()
    return changed


def order_for(session: Session, run_id: str, node_id: str) -> PurchaseOrder | None:
    return session.scalar(
        select(PurchaseOrder).where(PurchaseOrder.source_run_id == run_id, PurchaseOrder.source_node_id == node_id)
    )


def offer_for(session: Session, shift_id: str) -> ShiftOffer | None:
    return session.get(ShiftOffer, shift_id)
