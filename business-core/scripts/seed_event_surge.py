"""Event Surge demo scenario (AF-01): a believable Thursday-to-Sunday picture for CAFE001.

Story: farmers market Sunday Nov 1, 8 AM-2 PM. Thursday Oct 29 at 5 PM the closing shift
counts milk, beans and pastries (milk is low before the next morning's regular delivery); a
pitcher of oat milk is spilled at 5:40 PM. Sunday
already has a shift lead, a cashier, and one barista from noon. The café's busy-event
target is 4 people, so the morning is short by 2 and the early afternoon by 1.

Every row is synthetic and tagged with ``AF_EVENT_SURGE_20261101_``. January 2025 demo
data is never touched.

Usage (from business-core/, after ./scripts/seed_all.sh):
    PYTHONPATH=. python scripts/seed_event_surge.py            # seed (safe to repeat)
    PYTHONPATH=. python scripts/seed_event_surge.py --reset    # show what reset would remove
    PYTHONPATH=. python scripts/seed_event_surge.py --reset --yes
    PYTHONPATH=. python scripts/seed_event_surge.py --fresh    # reset + seed: demo-day one-liner

Reset removes the scenario rows plus everything the agent changed for CAFE001: its runs,
and the shifts, stock counts and movements created by approved agent steps.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.db.session import SessionLocal
from backend.models.cafe import Cafe
from backend.modules.agent.models import AgentApproval, AgentNodeRun, AgentPlanRun
from backend.modules.inventory.models import InventoryMovement, StockCount, StockCountLine
from backend.modules.purchasing.models import PurchaseOrder, PurchaseOrderLine, Receipt, ReceiptLine, ShiftOffer
from backend.modules.staffing.models import Employee, Shift
from scripts.seed_demo_operations import _seed_record

CAFE_ID = "CAFE001"
PREFIX = "AF_EVENT_SURGE_20261101_"
AS_OF = "2026-10-29T18:00:00-07:00"

COUNTED_AT = datetime.fromisoformat("2026-10-29T17:00:00-07:00")
COUNT_ID = f"{PREFIX}COUNT_THU_CLOSE"
COUNT_QUANTITIES = {
    "WHOLE_MILK": Decimal("3000.000"),
    "OAT_MILK": Decimal("1500.000"),
    "COFFEE_BEANS": Decimal("6000.000"),
    "DRIP_COFFEE": Decimal("1500.000"),
    "COLD_BREW_CONCENTRATE": Decimal("4000.000"),
    "BUTTER_CROISSANT": Decimal("30.000"),
    "BLUEBERRY_MUFFIN": Decimal("24.000"),
}
SPILL = {
    "movement_id": f"{PREFIX}MOVE_OAT_SPILL",
    "cafe_id": CAFE_ID,
    "ingredient_id": "OAT_MILK",
    "movement_type": "WASTE",
    "quantity_delta": Decimal("-250.000"),
    "is_estimated": False,
    "source_type": "waste_log",
    "source_id": f"{PREFIX}WASTE_001",
    "occurred_at": datetime.fromisoformat("2026-10-29T17:40:00-07:00"),
    "recorded_at": datetime.fromisoformat("2026-10-29T17:45:00-07:00"),
    "recorded_by": "DEMO_EMP_002",
    "reason": "Steamed pitcher spilled at close",
}
SUNDAY_SHIFTS = (
    # employee, start, end  (all PST, after the Nov 1 daylight-saving change)
    ("DEMO_EMP_002", "07:00", "15:00"),  # Jordan Brooks, shift lead
    ("DEMO_EMP_007", "08:00", "14:00"),  # Ava Thompson, cashier
    ("DEMO_EMP_005", "12:00", "16:00"),  # Nora Williams, barista (afternoon only)
)
SEEDED_AT = datetime.fromisoformat("2026-10-28T12:00:00-07:00")

# The Sunday market milk, confirmed by Willow Ridge (matches demo-assets/receiving/packing-slip-PS-58213).
DEMO_ORDER_ID = f"{PREFIX}PO_1042"
DEMO_ORDER_LINES = (
    # ingredient, item number, description, cases, case price
    ("WHOLE_MILK", "WR-1001", "Whole Milk, 1 L carton", 2, 2100),
    ("OAT_MILK", "WR-2240", "Oat Milk Barista Edition, 1 L carton", 1, 2820),
)


class EventSurgeSeedError(RuntimeError):
    pass


@dataclass(frozen=True)
class SeedResult:
    counts: int
    count_lines: int
    movements: int
    shifts: int
    orders: int = 0


@dataclass(frozen=True)
class ResetPlan:
    runs: list[str]
    shifts: list[str]
    counts: list[str]
    movements: list[str]
    orders: list[str] = field(default_factory=list)
    receipts: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.runs or self.shifts or self.counts or self.movements or self.orders or self.receipts)


def _shift_rows() -> list[dict]:
    rows = []
    for employee_id, start, end in SUNDAY_SHIFTS:
        rows.append(
            {
                "shift_id": f"{PREFIX}SHIFT_{employee_id[-3:]}",
                "cafe_id": CAFE_ID,
                "employee_id": employee_id,
                "start_at": datetime.fromisoformat(f"2026-11-01T{start}:00-08:00"),
                "end_at": datetime.fromisoformat(f"2026-11-01T{end}:00-08:00"),
                "status": "scheduled",
                "created_at": SEEDED_AT,
            }
        )
    return rows


def _require_prerequisites(session: Session) -> None:
    if session.get(Cafe, CAFE_ID) is None:
        raise EventSurgeSeedError("CAFE001 is missing: run ./scripts/seed_all.sh first")
    for employee_id, _, _ in SUNDAY_SHIFTS:
        if session.get(Employee, employee_id) is None:
            raise EventSurgeSeedError(f"{employee_id} is missing: run ./scripts/seed_all.sh first")


def seed_event_surge(session: Session) -> SeedResult:
    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    with transaction:
        _require_prerequisites(session)
        count = {
            "count_id": COUNT_ID,
            "cafe_id": CAFE_ID,
            "counted_at": COUNTED_AT,
            "recorded_at": datetime.fromisoformat("2026-10-29T17:15:00-07:00"),
            "counted_by": "DEMO_EMP_002",
            "status": "verified",
            "is_synthetic": True,
        }
        counts = int(_seed_record(session, StockCount, "count_id", count, f"count {COUNT_ID}"))
        session.flush()
        lines = sum(
            _seed_record(
                session,
                StockCountLine,
                "count_line_id",
                {
                    "count_line_id": f"{PREFIX}LINE_{ingredient_id}",
                    "count_id": COUNT_ID,
                    "cafe_id": CAFE_ID,
                    "ingredient_id": ingredient_id,
                    "counted_quantity": quantity,
                },
                f"count line {ingredient_id}",
            )
            for ingredient_id, quantity in COUNT_QUANTITIES.items()
        )
        movements = int(_seed_record(session, InventoryMovement, "movement_id", SPILL, "oat spill"))
        shifts = sum(
            _seed_record(session, Shift, "shift_id", row, f"shift {row['shift_id']}") for row in _shift_rows()
        )
        session.flush()
        orders = 0
        if session.get(PurchaseOrder, DEMO_ORDER_ID) is None:
            delivery = datetime.fromisoformat("2026-11-01T05:30:00-08:00")
            session.add(
                PurchaseOrder(
                    order_id=DEMO_ORDER_ID, cafe_id=CAFE_ID, po_number="PO-1042", supplier_id="WILLOW_RIDGE",
                    supplier_name="Willow Ridge Dairy Supply", status="confirmed", requested_delivery_at=delivery,
                    confirmed_delivery_at=delivery, sent_at=datetime.fromisoformat("2026-10-29T18:20:00-07:00"),
                    respond_at=datetime.fromisoformat("2026-10-29T18:20:08-07:00"),
                    responded_at=datetime.fromisoformat("2026-10-29T18:20:08-07:00"), pending_decision="confirmed",
                    total_cents=sum(cases * price for *_, cases, price in DEMO_ORDER_LINES), simulated=True,
                    note="Demo order for the Sunday market (synthetic).",
                    lines=[
                        PurchaseOrderLine(
                            line_no=n, ingredient_id=ingredient, supplier_item_number=item, description=description,
                            quantity=Decimal(6000 * cases), unit="mL", pack_size=Decimal(6000),
                            pack_name="case of 6 × 1 L", packs=cases, pack_price_cents=price,
                        )
                        for n, (ingredient, item, description, cases, price) in enumerate(DEMO_ORDER_LINES, start=1)
                    ],
                )
            )
            orders = 1
            session.flush()
    return SeedResult(counts, lines, movements, shifts, orders)


def plan_reset(session: Session) -> ResetPlan:
    runs = list(session.scalars(select(AgentPlanRun.run_id).where(AgentPlanRun.cafe_id == CAFE_ID)))
    agent_shifts: list[str] = []
    agent_counts: list[str] = []
    agent_movements: list[str] = []
    if runs:
        done = session.scalars(
            select(AgentNodeRun).where(
                AgentNodeRun.run_id.in_(runs), AgentNodeRun.status == "done", AgentNodeRun.replay_of.is_(None)
            )
        )
        for node in done:
            result = node.result if isinstance(node.result, dict) else {}
            if node.action == "schedule_shift" and result.get("shift_id"):
                agent_shifts.append(result["shift_id"])
            elif node.action == "create_stock_count" and result.get("count_id"):
                agent_counts.append(result["count_id"])
            elif node.action == "record_inventory_movement" and result.get("movement_id"):
                agent_movements.append(result["movement_id"])
    scenario_shifts = list(session.scalars(select(Shift.shift_id).where(Shift.shift_id.startswith(PREFIX))))
    scenario_counts = list(session.scalars(select(StockCount.count_id).where(StockCount.count_id.startswith(PREFIX))))
    scenario_moves = list(
        session.scalars(select(InventoryMovement.movement_id).where(InventoryMovement.movement_id.startswith(PREFIX)))
    )
    existing_agent_shifts = list(session.scalars(select(Shift.shift_id).where(Shift.shift_id.in_(agent_shifts))))
    existing_agent_counts = list(session.scalars(select(StockCount.count_id).where(StockCount.count_id.in_(agent_counts))))
    existing_agent_moves = list(
        session.scalars(select(InventoryMovement.movement_id).where(InventoryMovement.movement_id.in_(agent_movements)))
    )
    orders = list(session.scalars(
        select(PurchaseOrder.order_id).where(
            PurchaseOrder.cafe_id == CAFE_ID,
            (PurchaseOrder.order_id.startswith(PREFIX)) | (PurchaseOrder.source_run_id.in_(runs or [""])),
        )
    ))
    receipts = list(session.scalars(select(Receipt.receipt_id).where(Receipt.cafe_id == CAFE_ID)))
    receipt_moves = list(session.scalars(
        select(InventoryMovement.movement_id).where(
            InventoryMovement.cafe_id == CAFE_ID, InventoryMovement.source_type == "receipt"
        )
    ))
    return ResetPlan(
        orders=sorted(orders),
        receipts=sorted(receipts),
        runs=sorted(runs),
        shifts=sorted(set(scenario_shifts) | set(existing_agent_shifts)),
        counts=sorted(set(scenario_counts) | set(existing_agent_counts)),
        movements=sorted(set(scenario_moves) | set(existing_agent_moves) | set(receipt_moves)),
    )


def apply_reset(session: Session, plan: ResetPlan) -> None:
    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    with transaction:
        if plan.receipts:
            session.execute(delete(ReceiptLine).where(ReceiptLine.receipt_id.in_(plan.receipts)))
            session.execute(delete(Receipt).where(Receipt.receipt_id.in_(plan.receipts)))
        if plan.orders:
            session.execute(delete(PurchaseOrderLine).where(PurchaseOrderLine.order_id.in_(plan.orders)))
            session.execute(delete(PurchaseOrder).where(PurchaseOrder.order_id.in_(plan.orders)))
        if plan.runs:
            session.execute(delete(ShiftOffer).where(ShiftOffer.source_run_id.in_(plan.runs)))
            session.execute(delete(AgentApproval).where(AgentApproval.run_id.in_(plan.runs)))
            session.execute(delete(AgentNodeRun).where(AgentNodeRun.run_id.in_(plan.runs)))
            session.execute(delete(AgentPlanRun).where(AgentPlanRun.run_id.in_(plan.runs)))
        if plan.movements:
            session.execute(delete(InventoryMovement).where(InventoryMovement.movement_id.in_(plan.movements)))
        if plan.counts:
            session.execute(delete(StockCountLine).where(StockCountLine.count_id.in_(plan.counts)))
            session.execute(delete(StockCount).where(StockCount.count_id.in_(plan.counts)))
        if plan.shifts:
            session.execute(delete(Shift).where(Shift.shift_id.in_(plan.shifts), Shift.cafe_id == CAFE_ID))
        session.flush()


def _describe(plan: ResetPlan) -> str:
    return (
        f"{len(plan.runs)} agent run(s), {len(plan.shifts)} shift(s), "
        f"{len(plan.counts)} stock count(s), {len(plan.movements)} movement(s), "
        f"{len(plan.orders)} purchase order(s), {len(plan.receipts)} delivery check(s)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="remove the scenario and the agent's changes")
    parser.add_argument("--yes", action="store_true", help="actually delete (otherwise --reset is a dry run)")
    parser.add_argument("--fresh", action="store_true", help="reset (no prompt) and seed again")
    args = parser.parse_args()

    with SessionLocal() as session:
        if args.reset or args.fresh:
            plan = plan_reset(session)
            if plan.empty:
                print("Nothing to reset.")
            elif args.fresh or args.yes:
                apply_reset(session, plan)
                session.commit()
                print(f"Reset removed {_describe(plan)}.")
            else:
                print(f"Dry run: reset would remove {_describe(plan)}.")
                for label, ids in (("runs", plan.runs), ("shifts", plan.shifts), ("counts", plan.counts), ("movements", plan.movements)):
                    for item in ids:
                        print(f"  {label}: {item}")
                print("Run again with --yes to delete.")
                return 0
            if not args.fresh:
                return 0
        result = seed_event_surge(session)
        session.commit()
    print(
        "Event Surge scenario ready: "
        f"{result.counts} count, {result.count_lines} count lines, {result.movements} movement, "
        f"{result.shifts} Sunday shifts, {result.orders} demo order (PO-1042) created. Ask with knowledge as of {AS_OF}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
