"""Sending orders to (simulated) suppliers and offering shifts to (simulated) staff.

The simulator decides its answer when an order or offer is sent and reveals it after a short,
configurable delay (``settle_due``), so the owner sees "Sent, waiting for Bay Dairy" before
"Confirmed" or "Rejected". Every simulated record carries ``simulated=True``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from decimal import ROUND_CEILING, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.config import ENV_FILE
from backend.models.cafe import Cafe
from backend.modules.agent.cafe_policies import policies_for
from backend.modules.purchasing.models import PurchaseOrder, PurchaseOrderLine, Receipt, ShiftOffer


class SupplierNotFoundError(LookupError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DemoTiming(BaseSettings):
    """Demo only: hold simulated supplier replies back so "Waiting on <supplier>" stays on screen."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    demo_supplier_reply_delay_seconds: int = Field(default=0, ge=0)


@lru_cache
def demo_timing() -> DemoTiming:
    return DemoTiming()


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


def pack_size_for(cafe_id: str, supplier_id: str | None, ingredient_id: str) -> Decimal | None:
    """The supplier's pack size for an item, else the café's pack-size policy, else None (sold loose)."""
    try:
        catalog = get_supplier(cafe_id, supplier_id).get("items") or {}
    except SupplierNotFoundError:
        catalog = {}
    if ingredient_id in catalog:
        return Decimal(catalog[ingredient_id]["pack_size"])
    pack = policies_for(cafe_id).get("ordering", {}).get("pack_sizes", {}).get(ingredient_id)
    return Decimal(pack["size"]) if pack else None


def whole_packs(cafe_id: str, supplier_id: str | None, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Round each order line up to whole packs, so the quantity the owner approves is the one sent."""
    out = []
    for line in lines:
        pack_size = pack_size_for(cafe_id, supplier_id, line["ingredient_id"])
        quantity = Decimal(str(line["quantity"]))
        if pack_size and quantity % pack_size:
            packs = int((quantity / pack_size).to_integral_value(rounding=ROUND_CEILING))
            line = {**line, "quantity": f"{(pack_size * packs).normalize():f}"}
        out.append(line)
    return out


def _tz(session: Session, cafe_id: str) -> ZoneInfo:
    cafe = session.get(Cafe, cafe_id)
    return ZoneInfo(cafe.timezone if cafe else "UTC")


PO_NUMBER_START = 1040


def next_po_number(session: Session, cafe_id: str) -> str:
    """Human order numbers (PO-1043…), unique per café."""
    numbers = session.scalars(select(PurchaseOrder.po_number).where(PurchaseOrder.cafe_id == cafe_id))
    highest = max((int(n.split("-")[-1]) for n in numbers if n and n.split("-")[-1].isdigit()), default=PO_NUMBER_START)
    return f"PO-{highest + 1}"


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


def stock_problem(supplier: dict[str, Any], ingredient_id: str, requested: datetime, tz: ZoneInfo) -> str | None:
    """A supplier-side shortage the café can't see in advance (only revealed when it answers)."""
    for rule in supplier.get("out_of_stock", []):
        if rule["ingredient_id"] != ingredient_id:
            continue
        until = datetime.fromisoformat(rule["until"]).replace(tzinfo=tz)
        if requested.astimezone(tz) < until:
            return f"{supplier['name']} is out of {ingredient_id.replace('_', ' ').lower()} until {until.strftime('%a, %b %-d')}."
    return None


def supplier_options(session: Session, cafe_id: str, lines: list["OrderLineIn"], deliver_by: datetime | None) -> dict[str, Any]:
    """Who carries these items, what it would cost, and whether their published hours fit.

    Stock at the supplier is not known here: a supplier confirms it when it answers the order.
    """
    tz = _tz(session, cafe_id)
    usual = policies_for(cafe_id).get("ordering", {}).get("default_supplier_id")
    options = []
    for supplier in suppliers(cafe_id):
        catalog = supplier.get("items") or {}
        missing = [l.ingredient_id for l in lines if l.ingredient_id not in catalog]
        total = 0
        priced = []
        for line in lines:
            item = catalog.get(line.ingredient_id)
            if not item:
                continue
            packs = int((line.quantity / Decimal(item["pack_size"])).to_integral_value(rounding=ROUND_CEILING))
            total += packs * int(item["pack_price_cents"])
            priced.append({"ingredient_id": line.ingredient_id, "packs": packs, "pack_name": item["pack_name"],
                           "pack_price_cents": int(item["pack_price_cents"])})
        timing = delivery_problem(supplier, deliver_by, tz) if deliver_by else None
        earliest = None
        if deliver_by:
            local = deliver_by.astimezone(tz)
            earliest = supplier.get("earliest_delivery", {}).get(local.weekday())
        options.append({
            "supplier_id": supplier["supplier_id"], "name": supplier["name"], "usual": supplier["supplier_id"] == usual,
            "carries_all": not missing, "missing": missing, "total_cents": total, "lines": priced,
            "can_deliver_in_time": timing is None, "timing_note": timing,
            "earliest_delivery_that_day": earliest,
        })
    fits = [o for o in options if o["carries_all"] and o["can_deliver_in_time"]]
    fits.sort(key=lambda o: (not o["usual"], o["total_cents"]))
    options.sort(key=lambda o: (not o["usual"], not o["carries_all"], o["total_cents"]))
    return {
        "suppliers": options,
        "recommended_supplier_id": fits[0]["supplier_id"] if fits else usual,
        "note": "Suppliers confirm stock and delivery when they answer the order.",
    }


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
        problem = problem or stock_problem(supplier, line.ingredient_id, requested_delivery_at, tz)
        pack_size = Decimal(item["pack_size"])
        packs = int((line.quantity / pack_size).to_integral_value(rounding=ROUND_CEILING))
        total += packs * int(item["pack_price_cents"])
        order_lines.append(
            PurchaseOrderLine(
                line_no=number, ingredient_id=line.ingredient_id, quantity=pack_size * packs, unit=line.unit,
                supplier_item_number=item.get("item_number"), description=item.get("description"),
                pack_size=pack_size, pack_name=item["pack_name"], packs=packs,
                pack_price_cents=int(item["pack_price_cents"]),
            )
        )

    order = PurchaseOrder(
        order_id=order_id,
        cafe_id=cafe_id,
        po_number=next_po_number(session, cafe_id),
        supplier_id=supplier["supplier_id"],
        supplier_name=supplier["name"],
        status="sent",
        requested_delivery_at=requested_delivery_at,
        sent_at=sent_at,
        respond_at=sent_at
        + timedelta(seconds=int(supplier.get("respond_after_seconds", 8)) + demo_timing().demo_supplier_reply_delay_seconds),
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


def delivery_for(session: Session, order_id: str) -> Receipt | None:
    """The latest checked-in delivery against an order (None until a delivery is confirmed)."""
    return session.scalar(
        select(Receipt)
        .where(Receipt.order_id == order_id, Receipt.status == "confirmed")
        .order_by(Receipt.confirmed_at.desc())
        .limit(1)
    )


def offer_for(session: Session, shift_id: str) -> ShiftOffer | None:
    return session.get(ShiftOffer, shift_id)


OPEN_ORDER_STATUSES = ("sent", "confirmed", "partially_received")


def upcoming_deliveries(
    session: Session, cafe_id: str, start: datetime, end: datetime, ingredient_ids: list[str] | None = None
) -> list[dict[str, Any]]:
    """Open supplier orders and the café's regular deliveries arriving in [start, end), café time,
    earliest first. Sent orders are listed with their status: the supplier hasn't confirmed yet."""
    from backend.modules.inventory.models import InventoryItem

    tz = _tz(session, cafe_id)
    names = dict(session.execute(select(InventoryItem.ingredient_id, InventoryItem.name).where(InventoryItem.cafe_id == cafe_id)).all())
    out: list[dict[str, Any]] = []
    orders = session.scalars(
        select(PurchaseOrder).where(PurchaseOrder.cafe_id == cafe_id, PurchaseOrder.status.in_(OPEN_ORDER_STATUSES))
    )
    for order in orders:
        arrives = (order.confirmed_delivery_at or order.requested_delivery_at).astimezone(tz)
        if not start <= arrives < end:
            continue
        for line in order.lines:
            if ingredient_ids and line.ingredient_id not in ingredient_ids:
                continue
            out.append({"kind": "order", "arrives_at": arrives, "ingredient_id": line.ingredient_id,
                        "name": names.get(line.ingredient_id, line.ingredient_id), "quantity": line.quantity, "unit": line.unit,
                        "supplier": order.supplier_name, "po_number": order.po_number, "status": order.status})
    for rule in policies_for(cafe_id).get("standing_deliveries", []):
        if ingredient_ids and rule["ingredient_id"] not in ingredient_ids:
            continue
        hour, minute = (int(x) for x in rule.get("at", "06:00").split(":"))
        day = start.astimezone(tz).date()
        while day <= end.astimezone(tz).date():
            arrives = datetime.combine(day, time(hour, minute), tz)
            if start <= arrives < end and day.weekday() in rule["weekdays"]:
                out.append({"kind": "regular", "arrives_at": arrives, "ingredient_id": rule["ingredient_id"],
                            "name": names.get(rule["ingredient_id"], rule["ingredient_id"]), "quantity": Decimal(rule["quantity"]),
                            "unit": rule["unit"], "supplier": None, "po_number": None, "status": "regular"})
            day += timedelta(days=1)
    return sorted(out, key=lambda d: (d["arrives_at"], d["kind"] != "order", d["name"]))
