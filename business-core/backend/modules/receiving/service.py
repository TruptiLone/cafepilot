"""Receiving a delivery: what was ordered vs what the slip says vs what is actually there.

Muse transcribes the slip and the photo (``vision.py``). Everything here is code:
  1. match slip lines and photo items to the purchase order (item number, then product name),
  2. convert cases to base units ("Case of 6 × 1 L" → 6,000 mL),
  3. flag each line: match / needs count / short / short-shipped / over / not on order / missing /
     damaged, with a plain message,
  4. after the owner confirms, add ONLY the accepted quantity to stock (receipt movements tied to
     the receipt line, so confirming twice never adds stock twice), mark the order received or
     partially received, and draft a credit request for anything missing or rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.modules.inventory.models import InventoryItem
from backend.modules.inventory.schemas import InventoryMovementCreate
from backend.modules.inventory.service import InventoryService
from backend.modules.purchasing.models import PurchaseOrder, Receipt, ReceiptLine
from backend.modules.purchasing.service import settle_due
from backend.modules.receiving.vision import PhotoRead, SlipRead

IMAGE_DIR = Path("var/receiving")
ZERO = Decimal("0")


class ReceivingError(ValueError):
    """The receipt can't move forward (e.g. counts still needed, already confirmed)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Units and matching
# ---------------------------------------------------------------------------

_PACK = re.compile(r"(\d+)\s*[×x*]\s*(\d+(?:\.\d+)?)\s*(ml|l|kg|g)\b", re.IGNORECASE)
_SINGLE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|l|kg|g)\b", re.IGNORECASE)
_TO_BASE = {"ml": ("mL", Decimal(1)), "l": ("mL", Decimal(1000)), "g": ("g", Decimal(1)), "kg": ("g", Decimal(1000))}


def parse_pack(text: str | None) -> tuple[Decimal | None, Decimal | None, str | None]:
    """'Case of 6 × 1 L' → (6000, 1000, 'mL'): pack size and unit size in base units."""
    if not text:
        return None, None, None
    match = _PACK.search(text)
    if match:
        count, size, unit = int(match.group(1)), Decimal(match.group(2)), match.group(3).lower()
        base, factor = _TO_BASE[unit]
        return count * size * factor, size * factor, base
    match = _SINGLE.search(text)
    if match:
        size, unit = Decimal(match.group(1)), match.group(2).lower()
        base, factor = _TO_BASE[unit]
        return size * factor, size * factor, base
    return None, None, None


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def match_ingredient(text: str | None, catalog: dict[str, str]) -> str | None:
    """Ingredient whose name words all appear in the text; the most specific name wins."""
    if not text:
        return None
    words = _words(text)
    best, best_len = None, 0
    for ingredient_id, name in catalog.items():
        name_words = _words(name)
        if name_words and name_words <= words and len(name_words) > best_len:
            best, best_len = ingredient_id, len(name_words)
    return best


def _fmt(quantity: Decimal | None, unit: str | None, unit_size: Decimal | None) -> str:
    """6000 mL with 1 L units → '6 × 1 L (6 L)'; falls back to plain base units."""
    if quantity is None:
        return "unknown"
    if unit == "mL":
        litres = quantity / 1000
        text = f"{litres.normalize():f} L"
    elif unit == "g":
        text = f"{(quantity / 1000).normalize():f} kg"
    else:
        text = f"{quantity.normalize():f} {unit or ''}".strip()
    if unit_size:
        units = quantity / unit_size
        if units == units.to_integral_value():
            return f"{int(units)} cartons ({text})" if unit == "mL" else f"{int(units)} units ({text})"
    return text


# ---------------------------------------------------------------------------
# Building and comparing lines
# ---------------------------------------------------------------------------


@dataclass
class _Draft:
    ingredient_id: str | None
    description: str
    order_line_no: int | None = None
    item_number: str | None = None
    unit: str | None = None
    pack_size: Decimal | None = None
    unit_size: Decimal | None = None
    ordered: Decimal | None = None
    shipped: Decimal | None = None
    backordered: Decimal | None = None
    seen: Decimal | None = None
    may_hide_more: bool = False


def _catalog(session: Session, cafe_id: str) -> dict[str, str]:
    return {
        item.ingredient_id: item.name
        for item in session.scalars(select(InventoryItem).where(InventoryItem.cafe_id == cafe_id))
    }


def build_drafts(session: Session, receipt: Receipt, order: PurchaseOrder | None) -> list[_Draft]:
    catalog = _catalog(session, receipt.cafe_id)
    drafts: list[_Draft] = []
    by_item: dict[str, _Draft] = {}
    by_ingredient: dict[str, _Draft] = {}

    if order is not None:
        for line in order.lines:
            draft = _Draft(
                ingredient_id=line.ingredient_id,
                description=line.description or catalog.get(line.ingredient_id, line.ingredient_id),
                order_line_no=line.line_no,
                item_number=line.supplier_item_number,
                unit=line.unit,
                pack_size=line.pack_size,
                ordered=line.quantity,
            )
            drafts.append(draft)
            if line.supplier_item_number:
                by_item[line.supplier_item_number.upper()] = draft
            by_ingredient[line.ingredient_id] = draft

    slip = SlipRead.model_validate(receipt.slip_read) if receipt.slip_read else None
    for slip_line in slip.lines if slip else []:
        pack_size, unit_size, unit = parse_pack(slip_line.pack or slip_line.description)
        ingredient = match_ingredient(slip_line.description, catalog)
        draft = by_item.get((slip_line.item_number or "").upper()) or (by_ingredient.get(ingredient) if ingredient else None)
        if draft is None:
            draft = _Draft(ingredient_id=ingredient, description=slip_line.description or "Unknown item",
                           item_number=slip_line.item_number, unit=unit)
            drafts.append(draft)
            if ingredient:
                by_ingredient[ingredient] = draft
        draft.pack_size = draft.pack_size or pack_size
        draft.unit_size = unit_size or draft.unit_size
        draft.unit = draft.unit or unit
        size = draft.pack_size or pack_size
        if size is not None:
            if slip_line.shipped is not None:
                draft.shipped = Decimal(str(slip_line.shipped)) * size
            if slip_line.backordered is not None:
                draft.backordered = Decimal(str(slip_line.backordered)) * size

    photo = PhotoRead.model_validate(receipt.photo_read) if receipt.photo_read else None
    for item in photo.items if photo else []:
        ingredient = match_ingredient(f"{item.product or ''} {item.label_text or ''}", catalog)
        draft = by_ingredient.get(ingredient) if ingredient else None
        if draft is None:
            draft = _Draft(ingredient_id=ingredient, description=item.label_text or item.product or "Unknown item")
            drafts.append(draft)
            if ingredient:
                by_ingredient[ingredient] = draft
        unit_size = draft.unit_size or parse_pack(item.pack_label)[1] or Decimal(1000)
        draft.unit_size = draft.unit_size or unit_size
        draft.seen = (draft.seen or ZERO) + Decimal(str(item.visible_units)) * unit_size
        draft.may_hide_more = draft.may_hide_more or item.possibly_hidden_units
    return drafts


def evaluate(draft: _Draft, counted: Decimal | None, rejected: Decimal, has_slip: bool, has_photo: bool) -> tuple[str, Decimal | None, str]:
    """Status, accepted quantity (None until known) and a plain message for one line."""
    f = lambda q: _fmt(q, draft.unit, draft.unit_size)  # noqa: E731
    expected = draft.shipped if draft.shipped is not None else draft.ordered

    if draft.order_line_no is None:
        where = "on the slip" if draft.shipped is not None else "in the photo"
        return "not_on_order", ZERO, f"{draft.description} is {where} but not on our order. Don't accept it unless you want it."

    if counted is not None:
        arrived = counted
        source = "you counted"
    elif draft.seen is not None and not (draft.may_hide_more and expected is not None and draft.seen < expected):
        arrived = draft.seen
        source = "Muse can see"
    else:
        if draft.seen is not None:
            return "needs_count", None, (
                f"Muse can see {f(draft.seen)} of {draft.description.lower()}, but the "
                f"{'slip says' if draft.shipped is not None else 'order is for'} {f(expected)}"
                f" and the crate or case may hide more. Please count it."
            )
        if has_photo:
            return "needs_count", None, f"{draft.description} isn't visible in the photo. Please count it."
        return "needs_count", None, f"Count the {draft.description.lower()}: expecting {f(expected)}."

    if expected is None:
        expected = draft.ordered
    good = max(arrived - rejected, ZERO)
    if rejected > 0:
        status = "damaged"
        message = f"{source.capitalize()} {f(arrived)}; {f(rejected)} rejected. Accept {f(good)}."
    elif has_slip and draft.shipped is None and arrived == 0:
        status = "missing"
        message = f"{draft.description} is on our order but not on the slip and not here."
    elif draft.ordered is not None and arrived > draft.ordered:
        status = "over"
        good = draft.ordered
        message = f"{source.capitalize()} {f(arrived)}, but we ordered {f(draft.ordered)}. Accept the ordered amount and send the rest back, or keep it and pay extra."
    elif expected is not None and arrived < expected:
        status = "short"
        message = f"{source.capitalize()} {f(arrived)}, but the slip says {f(expected)}: {f(expected - arrived)} short."
    elif draft.ordered is not None and draft.shipped is not None and draft.shipped < draft.ordered:
        status = "short_shipped"
        message = f"The supplier shipped {f(draft.shipped)} of the {f(draft.ordered)} ordered; {f(draft.ordered - draft.shipped)} back-ordered."
    else:
        status = "match"
        message = f"{source.capitalize()} {f(arrived)}: matches the order."
    return status, good, message


def recompare(session: Session, receipt: Receipt) -> Receipt:
    """Rebuild every line from the order, the slip and the photo, keeping the owner's counts."""
    order = session.get(PurchaseOrder, receipt.order_id) if receipt.order_id else None
    previous = {
        (l.ingredient_id or l.description): (l.counted_quantity, l.rejected_quantity or ZERO, l.rejected_reason)
        for l in receipt.lines
    }
    drafts = build_drafts(session, receipt, order)
    receipt.lines.clear()
    session.flush()
    for number, draft in enumerate(drafts, start=1):
        counted, rejected, reason = previous.get(draft.ingredient_id or draft.description, (None, ZERO, None))
        status, accepted, message = evaluate(
            draft, counted, rejected or ZERO, receipt.slip_read is not None, receipt.photo_read is not None
        )
        receipt.lines.append(
            ReceiptLine(
                line_no=number,
                ingredient_id=draft.ingredient_id,
                order_line_no=draft.order_line_no,
                supplier_item_number=draft.item_number,
                description=draft.description,
                unit=draft.unit,
                pack_size=draft.pack_size,
                unit_size=draft.unit_size,
                ordered_quantity=draft.ordered,
                slip_shipped_quantity=draft.shipped,
                slip_backordered_quantity=draft.backordered,
                seen_quantity=draft.seen,
                seen_may_hide_more=draft.may_hide_more,
                counted_quantity=counted,
                rejected_quantity=rejected or ZERO,
                rejected_reason=reason,
                accepted_quantity=accepted,
                status=status,
                message=message,
            )
        )
    session.flush()
    return receipt


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def start_receipt(session: Session, cafe_id: str, order_id: str | None = None) -> Receipt:
    if order_id is not None:
        order = session.get(PurchaseOrder, order_id)
        if order is None or order.cafe_id != cafe_id:
            raise LookupError(f"purchase order not found: {order_id}")
    receipt = Receipt(
        receipt_id=f"RCV-{uuid4().hex[:10].upper()}", cafe_id=cafe_id, order_id=order_id,
        status="draft", started_at=_now(),
    )
    session.add(receipt)
    session.flush()
    return receipt


def _save_image(receipt: Receipt, kind: str, image: bytes, mime: str) -> str:
    folder = IMAGE_DIR / receipt.receipt_id
    folder.mkdir(parents=True, exist_ok=True)
    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(mime, ".img")
    path = folder / f"{kind}{suffix}"
    path.write_bytes(image)
    return str(path)


def attach_slip(session: Session, receipt: Receipt, slip: SlipRead, model: str, image: bytes | None = None,
                mime: str = "image/jpeg") -> Receipt:
    _require_draft(receipt)
    receipt.slip_read = slip.model_dump(mode="json")
    receipt.slip_number = slip.packing_slip_number
    receipt.slip_supplier = slip.supplier
    receipt.slip_po_number = slip.customer_po_number
    receipt.slip_delivery = " ".join(x for x in (slip.delivery_date, slip.delivery_window) if x) or None
    receipt.model = model
    if image is not None:
        receipt.slip_image_path = _save_image(receipt, "slip", image, mime)
    if receipt.order_id is None and slip.customer_po_number:
        order = session.scalar(
            select(PurchaseOrder).where(
                PurchaseOrder.cafe_id == receipt.cafe_id, PurchaseOrder.po_number == slip.customer_po_number
            )
        )
        if order is not None:
            receipt.order_id = order.order_id
    return recompare(session, receipt)


def attach_photo(session: Session, receipt: Receipt, photo: PhotoRead, model: str, image: bytes | None = None,
                 mime: str = "image/jpeg") -> Receipt:
    _require_draft(receipt)
    receipt.photo_read = photo.model_dump(mode="json")
    receipt.model = model
    if image is not None:
        receipt.photo_image_path = _save_image(receipt, "photo", image, mime)
    return recompare(session, receipt)


def set_count(session: Session, receipt: Receipt, line_no: int, *, counted: Decimal | None = None,
              rejected: Decimal | None = None, reason: str | None = None) -> Receipt:
    _require_draft(receipt)
    line = next((l for l in receipt.lines if l.line_no == line_no), None)
    if line is None:
        raise LookupError(f"line {line_no} not found")
    if counted is not None:
        line.counted_quantity = counted
    if rejected is not None:
        if rejected > (line.counted_quantity if line.counted_quantity is not None else line.seen_quantity or ZERO):
            raise ReceivingError("You can't reject more than arrived.")
        line.rejected_quantity = rejected
        line.rejected_reason = reason
    session.flush()
    return recompare(session, receipt)


def _require_draft(receipt: Receipt) -> None:
    if receipt.status != "draft":
        raise ReceivingError(f"This delivery was already {receipt.status}.")


def confirm(session: Session, receipt: Receipt, *, confirmed_by: str = "owner", channel: str = "web") -> Receipt:
    """Owner approved: only accepted quantities become stock; shortfalls become a credit request."""
    _require_draft(receipt)
    if receipt.order_id is None:
        raise ReceivingError("Match this delivery to an order first (the slip's PO number wasn't found).")
    waiting = [l for l in receipt.lines if l.status == "needs_count"]
    if waiting:
        raise ReceivingError("Count these first: " + ", ".join(l.description for l in waiting) + ".")

    order = session.get(PurchaseOrder, receipt.order_id)
    received_at = _now()
    inventory = InventoryService(session)
    for line in receipt.lines:
        if line.ingredient_id and line.order_line_no is not None and line.accepted_quantity and line.accepted_quantity > 0:
            inventory.create_movement(
                InventoryMovementCreate(
                    cafe_id=receipt.cafe_id,
                    ingredient_id=line.ingredient_id,
                    movement_type="RECEIPT",
                    quantity_delta=line.accepted_quantity,
                    source_type="receipt",
                    source_id=f"{receipt.receipt_id}:{line.line_no}",
                    occurred_at=received_at,
                    recorded_by=confirmed_by,
                    reason=f"Received against {order.po_number or order.order_id}"
                    + (f", slip {receipt.slip_number}" if receipt.slip_number else ""),
                )
            )
            order_line = next((o for o in order.lines if o.line_no == line.order_line_no), None)
            if order_line is not None:
                order_line.received_quantity = (order_line.received_quantity or ZERO) + line.accepted_quantity

    fully = all((o.received_quantity or ZERO) >= o.quantity for o in order.lines)
    order.status = "received" if fully else "partially_received"

    credit_lines = []
    for line in receipt.lines:
        if line.order_line_no is None or line.ordered_quantity is None:
            continue
        missing = line.ordered_quantity - (line.accepted_quantity or ZERO)
        if missing > 0:
            reason = {"damaged": "damaged / rejected", "short_shipped": "back-ordered"}.get(line.status, "short")
            credit_lines.append({
                "item_number": line.supplier_item_number,
                "description": line.description,
                "quantity": f"{missing.normalize():f}",
                "unit": line.unit,
                "display": _fmt(missing, line.unit, line.unit_size),
                "reason": line.rejected_reason or reason,
            })
    if credit_lines:
        receipt.credit_request = {
            "credit_ref": f"CR-{(receipt.slip_number or receipt.receipt_id).split('-')[-1]}",
            "supplier": order.supplier_name,
            "against_slip": receipt.slip_number,
            "against_po": order.po_number,
            "lines": credit_lines,
            "status": "drafted",
            "simulated": order.simulated,
            "note": "Drafted from the receiving check; sent only when you approve it.",
        }

    receipt.status = "confirmed"
    receipt.confirmed_at = received_at
    receipt.confirmed_by = confirmed_by
    receipt.channel = channel
    session.flush()
    return receipt


def send_credit_request(session: Session, receipt: Receipt) -> Receipt:
    if receipt.credit_request is None:
        raise ReceivingError("There's no credit request for this delivery.")
    request = dict(receipt.credit_request)
    if request.get("status") == "drafted":
        request["status"] = "sent"
        request["sent_at"] = _now().isoformat()
        receipt.credit_request = request
        session.flush()
    return receipt


def open_orders(session: Session, cafe_id: str) -> list[PurchaseOrder]:
    settle_due(session, cafe_id=cafe_id)
    return list(
        session.scalars(
            select(PurchaseOrder)
            .where(PurchaseOrder.cafe_id == cafe_id, PurchaseOrder.status.in_(("confirmed", "partially_received")))
            .order_by(PurchaseOrder.requested_delivery_at)
        )
    )


def summary(receipt: Receipt) -> dict[str, Any]:
    """Counts for the header: how many lines match, need a count, have a problem."""
    statuses = [l.status for l in receipt.lines]
    return {
        "lines": len(statuses),
        "match": statuses.count("match"),
        "needs_count": statuses.count("needs_count"),
        "problems": sum(s in ("short", "short_shipped", "over", "not_on_order", "missing", "damaged") for s in statuses),
    }
