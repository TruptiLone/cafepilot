"""End-to-end glasses test with one real milk carton (test only; the demo data and plans are unchanged).

Run from business-core/:
    PYTHONPATH=. python scripts/glasses_test_order.py order        # 1 × 1.7 L whole milk, waits for your OK
    PYTHONPATH=. python scripts/glasses_test_order.py slip PO-1041 # A4 packing slip (PNG + PDF) for that order

`order` makes a one-step plan: send 1 carton of whole milk to "Glasses Test Dairy (test)", a simulated
supplier only this script names (see TEST_SUPPLIERS in cafe_policies.py). Muse doesn't plan it: the quantity
is fixed here, so the validator's "quantities come from calculate_shortfall" rule is waived for this one
test step and the run says so. You approve it in the web app like any other change; the simulated supplier
confirms a few seconds later.

`slip` draws the supplier's packing slip for a confirmed order in the layout the delivery check reads
(PS number, customer PO, item #, description, pack, ordered / shipped / back-ordered). Files go to
var/test-slips/ (git-ignored). `seed_event_surge.py --fresh` removes the test order with the other runs.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.orm import Session

import backend.main  # noqa: F401  (loads every model)
from backend.models.cafe import Cafe
from backend.db.session import SessionLocal
from backend.modules.agent.executor import create_run, execute
from backend.modules.agent.models import AgentPlanRun
from backend.modules.agent.planner import Plan
from backend.modules.agent.validator import ValidationResult, validate_plan
from backend.modules.purchasing.models import PurchaseOrder
from backend.modules.purchasing.service import get_supplier

CAFE_ID = "CAFE001"
SUPPLIER_ID = "GLASSES_TEST"
OUT_DIR = Path(__file__).resolve().parents[1] / "var" / "test-slips"
# Rules a fixed-quantity test order can't meet; any other issue stops the script.
WAIVED = {"unverified_quantity", "missing_prerequisite"}


def make_order(session: Session, delivery_in_hours: int = 1) -> AgentPlanRun:
    tz = ZoneInfo(session.get(Cafe, CAFE_ID).timezone)
    item = get_supplier(CAFE_ID, SUPPLIER_ID)["items"]["WHOLE_MILK"]
    now = datetime.now(tz)
    deliver = (now + timedelta(hours=delivery_in_hours)).replace(minute=0, second=0, microsecond=0)
    plan = Plan.model_validate({
        "status": "plan",
        "goal": "Test order: 1 carton of whole milk for the glasses check",
        "assumptions": [
            "Test only: this order was made by scripts/glasses_test_order.py, not planned by Muse.",
            f"The quantity is fixed at 1 × {item['pack_name']} ({item['description']}), not worked out from stock.",
        ],
        "nodes": [{
            "id": "test_carton_order", "action": "propose_supplier_order",
            "args": {"cafe_id": CAFE_ID, "supplier_id": SUPPLIER_ID,
                     "lines": [{"ingredient_id": "WHOLE_MILK", "quantity": item["pack_size"], "unit": "mL"}],
                     "requested_delivery_at": deliver.isoformat(),
                     "note": "Test order for the glasses delivery check (one real carton)."},
            "reason": "Test the glasses delivery check end to end with one real milk carton.",
            "only_if": "You are running the glasses test (simulated supplier, nothing is really ordered).",
        }],
    })
    checked = validate_plan(plan, CAFE_ID, timezone=tz.key)
    other = [i for i in checked.issues if i.code not in WAIVED]
    if other:
        sys.exit("Validator found more than the test waiver allows: " + "; ".join(i.message for i in other))
    waived = sorted({i.code for i in checked.issues})
    validation = ValidationResult(
        valid=True, nodes=checked.nodes, edges=checked.edges, levels=checked.levels,
        warnings=checked.warnings + [f"Test order: rules waived by the test script: {', '.join(waived)}."],
    )
    run = create_run(session, query="Test: order one 1.7 L carton of whole milk (glasses test)", cafe_id=CAFE_ID,
                     as_of=datetime.now(timezone.utc), plan=plan, validation=validation,
                     model="test script (no model)", attempts=0)
    run = execute(session, run.run_id)
    print(f"{run.run_id}: waiting for your OK · 1 {item['pack_name']} whole milk from "
          f"{get_supplier(CAFE_ID, SUPPLIER_ID)['name']}, delivery {deliver:%a %b %-d, %-I:%M %p}")
    print(f"Approve it: http://localhost:5173/agent?run={run.run_id}")
    return run


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    name = "Arial Bold.ttf" if bold else "Arial.ttf"
    for folder in ("/System/Library/Fonts/Supplemental/", "/Library/Fonts/"):
        try:
            return ImageFont.truetype(folder + name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def draw_slip(session: Session, po_number: str, out_dir: Path = OUT_DIR) -> Path:
    order = session.scalar(select(PurchaseOrder).where(PurchaseOrder.cafe_id == CAFE_ID,
                                                       PurchaseOrder.po_number == po_number.upper()))
    if order is None:
        sys.exit(f"{po_number} isn't in the system.")
    if order.status not in ("confirmed", "partially_received"):
        sys.exit(f"{order.po_number} is '{order.status}'. Print the slip once the supplier has confirmed it.")
    cafe = session.get(Cafe, CAFE_ID)
    tz = ZoneInfo(cafe.timezone)
    arrives = (order.confirmed_delivery_at or order.requested_delivery_at).astimezone(tz)
    lines = sorted(order.lines, key=lambda l: l.line_no)
    slip_number = f"PS-{70000 + int(order.po_number.split('-')[-1])}"

    # A4 at 150 dpi. Numbers the check must read (PS, PO, quantities) are set large.
    W, H = 1240, 1754
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    L, R = 100, W - 100
    def text(x, y, s, size, bold=False, anchor="la", fill="black"):
        d.text((x, y), s, fill=fill, font=_font(bold, size), anchor=anchor)

    text(L, 90, order.supplier_name, 44, True)
    text(R, 86, "PACKING SLIP", 52, True, "ra")
    text(L, 150, "Simulated supplier for the CaféPilot glasses test", 20)
    text(R, 150, "Keep refrigerated  ·  33–40 °F", 20, anchor="ra")
    d.line([(L, 200), (R, 200)], fill="black", width=2)

    text(L, 230, "SHIP TO", 22, True)
    for i, s in enumerate([cafe.name, "412 Franklin St", "Santa Clara, CA 95050"]):
        text(L, 264 + 32 * i, s, 24)
    fields = [("Packing slip #", slip_number), ("Customer PO #", order.po_number),
              ("Order date", order.sent_at.astimezone(tz).strftime("%a %b %-d, %Y")),
              ("Delivery date", arrives.strftime("%a %b %-d, %Y")),
              ("Delivery time", arrives.strftime("%-I:%M %p"))]
    for i, (k, v) in enumerate(fields):
        big = i < 2
        y = 228 + 46 * i
        text(640, y + (6 if big else 2), k, 22)
        text(R, y, v, 34 if big else 24, True, "ra")

    top = 500
    d.rectangle([L, top, R, top + 64], fill=(232, 232, 232))
    cols = [(L + 10, "Item #", "la"), (L + 170, "Description", "la"), (L + 450, "Pack", "la"),
            (R - 230, "Ordered", "ma"), (R - 130, "Shipped", "ma"), (R - 40, "Back-ordered", "ma")]
    for x, label, anchor in cols:
        if label == "Back-ordered":
            text(x, top + 8, "Back-", 20, True, anchor); text(x, top + 32, "ordered", 20, True, anchor)
        else:
            text(x, top + 20, label, 22, True, anchor)
    total_units = 0
    for i, line in enumerate(lines):
        y = top + 90 + 80 * i
        packs = line.packs or 1
        pack = (line.pack_name or "")[:1].upper() + (line.pack_name or "")[1:]
        text(L + 10, y, line.supplier_item_number or "", 24)
        text(L + 170, y, line.description or line.ingredient_id, 24)
        text(L + 450, y, pack, 24)
        for x, value in [(R - 230, packs), (R - 130, packs), (R - 40, 0)]:
            text(x, y - 6, str(value), 34, True, "ma")
        d.line([(L, y + 50), (R, y + 50)], fill="black", width=1)
        total_units += packs
    y = top + 90 + 80 * len(lines)
    unit = (lines[0].pack_name or "").partition(" of ")[2] if lines else ""
    text(L, y, f"Total packs shipped: {sum(l.packs or 1 for l in lines)}", 24, True)
    text(R, y, f"Total units: {total_units} × {unit.split('× ')[-1]}" if unit else "", 24, True, "ra")

    y += 70
    text(L, y, "Notes", 22, True)
    text(L, y + 34, "Please count all items at delivery. Report shortages or damage within 24 hours.", 21)
    text(L, y + 64, "Credits are issued against this packing slip number.", 21)
    box = y + 130
    d.rectangle([L, box, R, box + 210], outline="black", width=2)
    text(L + 20, box + 16, "RECEIVING  (completed by customer)", 22, True)
    for row, (left, right) in enumerate([("Items received:", "Product temp (°F):"), ("Received by:", "Time:")]):
        yy = box + 70 + 56 * row
        text(L + 20, yy, left, 21); d.line([(L + 210, yy + 24), (L + 470, yy + 24)], fill="black")
        text(L + 540, yy, right, 21); d.line([(L + 740, yy + 24), (R - 20, yy + 24)], fill="black")
    text(W // 2, H - 90, "Test document for the CaféPilot glasses check. Simulated supplier; nothing was really ordered.",
         17, anchor="ma", fill=(110, 110, 110))

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / f"packing-slip-{slip_number}-{order.po_number}"
    im.save(stem.with_suffix(".png"), dpi=(150, 150))
    im.save(stem.with_suffix(".pdf"), resolution=150)  # A4 page
    print(f"{slip_number} for {order.po_number} ({order.supplier_name}):")
    for line in lines:
        print(f"  {line.supplier_item_number} {line.description} · {line.packs} × {line.pack_name}")
    print(f"  {stem.with_suffix('.pdf')}\n  {stem.with_suffix('.png')}")
    return stem.with_suffix(".png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    order = sub.add_parser("order", help="make the 1-carton test order (waits for your OK)")
    order.add_argument("--delivery-in-hours", type=int, default=1)
    slip = sub.add_parser("slip", help="draw the A4 packing slip for a confirmed order")
    slip.add_argument("po_number")
    args = parser.parse_args()
    with SessionLocal() as session:
        if args.command == "order":
            make_order(session, args.delivery_in_hours)
        else:
            draw_slip(session, args.po_number)


if __name__ == "__main__":
    main()
