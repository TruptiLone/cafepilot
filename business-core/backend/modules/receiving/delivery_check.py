"""A delivery check from two glasses photos: the packing slip and the delivery.

  1. Muse Spark reads the slip; code finds the order by its PO number.
  2. SAM 3.1 + Muse Spark count the delivery photo (``counting.py``); code adds up the totals.
  3. Code compares the order, the slip and the count (``service.recompare``).
  4. Code decides the outcome:
       received          every line matches and every count is confident: confirmed, stock added,
                         the order is marked received
       needs_owner       something is short, over, damaged or not sure: the receipt waits for the
                         owner's count or OK; nothing changes in stock yet
       order_not_found   the slip's PO number isn't in the system: everything is read and counted,
                         nothing is confirmed, and it is logged

The glasses line is written by code from those results, never by the model.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from sqlalchemy.orm import Session

from backend.modules.purchasing.models import PurchaseOrder, Receipt
from backend.modules.receiving import service
from backend.modules.receiving.counting import Product, count_delivery, noun_for
from backend.modules.receiving.vision import read_photo, read_slip

logger = logging.getLogger("cafepilot.receiving.delivery_check")

AUTO_CONFIRMED_BY = "glasses check (all matched)"
_WORDS = {
    "needs_count": "please count",
    "short": "short",
    "short_shipped": "back-ordered",
    "over": "more than ordered",
    "missing": "missing",
    "damaged": "damaged",
    "not_on_order": "not on the order",
}


def products_to_count(session: Session, receipt: Receipt) -> list[Product]:
    """What to look for in the photo: the order's lines, or the slip's when there is no order."""
    catalog = service._catalog(session, receipt.cafe_id)
    products = []
    for line in receipt.lines:
        if receipt.order_id is not None and line.order_line_no is None:
            continue  # on the slip but not the order: not counted, flagged by the comparison
        name = catalog.get(line.ingredient_id or "") or service._product(line.description)
        products.append(Product(name=name, noun=noun_for(line.ingredient_id, line.description)))
    return products


def _glasses_line(receipt: Receipt, outcome: str, po: str | None) -> str:
    if outcome == "order_not_found":
        what = f"Order {po} isn't in the system" if po else "The slip has no PO number we could read"
        return f"{what}. Slip read and items counted; nothing added to stock."
    label = po or "This delivery"
    if outcome == "received":
        return f"{label} received: all {len(receipt.lines)} items match. Stock updated."
    issues = [f"{service._product(l.description)}: {_WORDS.get(l.status, l.status)}"
              for l in receipt.lines if l.status != "match"]
    if not issues:  # every line matches, but the count wasn't sure enough to finish on its own
        return f"{label} matches the order, but the count isn't certain. Check it and confirm."
    return f"{label} needs your OK. " + "; ".join(issues) + "."


def check_delivery(session: Session, client: Any, model: str, receipt: Receipt,
                   slip_image: bytes, photo_image: bytes, mime: str = "image/jpeg") -> dict[str, Any]:
    """Run the whole check on a draft receipt. Raises ReceivingError / VisionError when it can't go on."""
    slip, _ = read_slip(client, model, slip_image, mime)
    receipt = service.attach_slip(session, receipt, slip, model, slip_image, mime)

    products = products_to_count(session, receipt)
    if products:
        photo, groups = count_delivery(client, model, photo_image, mime, products)
        method = "sam+spark"
    else:  # the slip had no lines we could read: Spark's own photo read
        photo, _ = read_photo(client, model, photo_image, mime)
        groups, method = [], "spark"
    receipt = service.attach_photo(session, receipt, photo, model, photo_image, mime)

    order = session.get(PurchaseOrder, receipt.order_id) if receipt.order_id else None
    if order is None:
        outcome = "order_not_found"
        logger.warning("Delivery check %s: order not found (slip %s, PO on slip %s). Read and counted; not confirmed.",
                       receipt.receipt_id, receipt.slip_number, receipt.slip_po_number)
    elif (receipt.lines and all(l.status == "match" for l in receipt.lines)
          and groups and all(g.confident and not g.error for g in groups)):
        receipt = service.confirm(session, receipt, confirmed_by=AUTO_CONFIRMED_BY, channel="glasses")
        outcome = "received"
    else:
        outcome = "needs_owner"
    session.flush()

    po = order.po_number if order else receipt.slip_po_number
    return {
        "outcome": outcome,
        "order_found": order is not None,
        "po_number": po,
        "slip_number": receipt.slip_number,
        "receipt_id": receipt.receipt_id,
        "receipt_status": receipt.status,
        "order_status": order.status if order else None,
        "count_method": method,
        "counts": [asdict(g) for g in groups],
        "glasses_line": _glasses_line(receipt, outcome, po),
    }
