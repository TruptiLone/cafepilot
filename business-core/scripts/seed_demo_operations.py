"""Seed deterministic synthetic operations for the CAFE001 demo environment."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.session import SessionLocal
from backend.models.cafe import Cafe
from backend.modules.inventory.models import (
    InventoryItem,
    InventoryMovement,
    StockCount,
    StockCountLine,
)
from backend.modules.sales.models import MenuProduct, Sale, SaleItem
from backend.modules.staffing.models import Employee, Shift

CAFE_ID = "CAFE001"
DEMO_PREFIX = "DEMO_"
WEEK_START = date(2025, 1, 6)
UNKNOWN_INGREDIENT_ID = "TEA_BAG"

BASELINE_QUANTITIES = {
    "COFFEE_BEANS": Decimal("7800.000"),
    "DRIP_COFFEE": Decimal("9200.000"),
    "WHOLE_MILK": Decimal("18000.000"),
    "SKIM_MILK": Decimal("9000.000"),
    "OAT_MILK": Decimal("6500.000"),
    "ALMOND_MILK": Decimal("5200.000"),
    "VANILLA_SYRUP": Decimal("2800.000"),
    "CARAMEL_SYRUP": Decimal("2600.000"),
    "CHOCOLATE_SAUCE": Decimal("3400.000"),
    "COLD_BREW_CONCENTRATE": Decimal("7600.000"),
    "CHAI_CONCENTRATE": Decimal("5400.000"),
    "MATCHA_POWDER": Decimal("1100.000"),
    "BLUEBERRY_MUFFIN": Decimal("24.000"),
    "CHOCOLATE_CHIP_MUFFIN": Decimal("22.000"),
    "BANANA_NUT_MUFFIN": Decimal("18.000"),
    "BANANA_BREAD": Decimal("20.000"),
    "LEMON_LOAF": Decimal("18.000"),
    "BUTTER_CROISSANT": Decimal("28.000"),
}

PARTIAL_QUANTITIES = {
    "COFFEE_BEANS": Decimal("5200.000"),
    "WHOLE_MILK": Decimal("11200.000"),
    "OAT_MILK": Decimal("4800.000"),
    "BLUEBERRY_MUFFIN": Decimal("14.000"),
    "BUTTER_CROISSANT": Decimal("16.000"),
}

EMPLOYEE_DEFINITIONS = (
    ("DEMO_EMP_001", "Maya Chen", "general_manager"),
    ("DEMO_EMP_002", "Jordan Brooks", "shift_lead"),
    ("DEMO_EMP_003", "Sofia Ramirez", "barista"),
    ("DEMO_EMP_004", "Ethan Patel", "barista"),
    ("DEMO_EMP_005", "Nora Williams", "barista"),
    ("DEMO_EMP_006", "Liam Kim", "kitchen"),
    ("DEMO_EMP_007", "Ava Thompson", "cashier"),
)

SHIFT_PATTERNS = (
    ("DEMO_EMP_001", range(7), time(6, 30), time(14, 30)),
    ("DEMO_EMP_002", (0, 1, 2, 3, 4), time(7), time(15)),
    ("DEMO_EMP_003", (0, 1, 3, 4, 5), time(8), time(16)),
    ("DEMO_EMP_004", (2, 3, 4, 5, 6), time(10), time(18)),
    ("DEMO_EMP_005", (0, 1, 3, 5, 6), time(12), time(20)),
    ("DEMO_EMP_006", (1, 3, 5, 6), time(6), time(12)),
    ("DEMO_EMP_007", (2, 3, 4, 6), time(14), time(21)),
)

MOVEMENT_DEFINITIONS = (
    ("BEANS_RECEIPT_1", "COFFEE_BEANS", "RECEIPT", "5000.000", 1, 10, "supplier_delivery", "Accepted coffee delivery"),
    ("BEANS_WASTE_1", "COFFEE_BEANS", "WASTE", "-120.000", 2, 17, "waste_log", "Dial-in and spill waste"),
    ("BEANS_ADJUST_1", "COFFEE_BEANS", "ADJUSTMENT", "80.000", 3, 16, "inventory_review", "Fictional recount correction"),
    ("MILK_RECEIPT_1", "WHOLE_MILK", "RECEIPT", "12000.000", 1, 9, "supplier_delivery", "Accepted dairy delivery"),
    ("MILK_WASTE_1", "WHOLE_MILK", "WASTE", "-1000.000", 3, 18, "waste_log", "Expired open containers"),
    ("OAT_RECEIPT_1", "OAT_MILK", "RECEIPT", "6000.000", 2, 9, "supplier_delivery", "Accepted alternative milk delivery"),
    ("UNKNOWN_TEA_RECEIPT", UNKNOWN_INGREDIENT_ID, "RECEIPT", "100.000", 1, 11, "supplier_delivery", "Accepted tea delivery without stock anchor"),
    ("BEANS_RECEIPT_2", "COFFEE_BEANS", "RECEIPT", "5000.000", 4, 10, "supplier_delivery", "Accepted coffee delivery"),
    ("BEANS_WASTE_2", "COFFEE_BEANS", "WASTE", "-90.000", 5, 17, "waste_log", "Dial-in and spill waste"),
    ("MILK_RECEIPT_2", "WHOLE_MILK", "RECEIPT", "12000.000", 4, 9, "supplier_delivery", "Accepted dairy delivery"),
    ("MILK_WASTE_2", "WHOLE_MILK", "WASTE", "-800.000", 6, 18, "waste_log", "End-of-week spoilage"),
    ("OAT_ADJUST_1", "OAT_MILK", "ADJUSTMENT", "-150.000", 5, 16, "inventory_review", "Fictional recount correction"),
)

SALE_HOURS = (7, 7, 7, 8, 8, 8, 9, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19)


class DemoSeedError(ValueError):
    """Raised when prerequisites or existing demo records are inconsistent."""


@dataclass(frozen=True)
class DemoPlan:
    counts: tuple[dict[str, object], ...]
    count_lines: tuple[dict[str, object], ...]
    movements: tuple[dict[str, object], ...]
    sales: tuple[dict[str, object], ...]
    sale_items: tuple[dict[str, object], ...]
    employees: tuple[dict[str, object], ...]
    shifts: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class DemoSeedResult:
    counts_created: int
    count_lines_created: int
    movements_created: int
    sales_created: int
    sale_items_created: int
    employees_created: int
    shifts_created: int


def _local_datetime(cafe_timezone: ZoneInfo, day_offset: int, at_time: time) -> datetime:
    return datetime.combine(
        WEEK_START + timedelta(days=day_offset),
        at_time,
        tzinfo=cafe_timezone,
    )


def _build_inventory_plan(cafe_timezone: ZoneInfo) -> tuple[tuple[dict, ...], tuple[dict, ...], tuple[dict, ...]]:
    baseline_at = _local_datetime(cafe_timezone, 0, time(5, 30))
    partial_at = _local_datetime(cafe_timezone, 4, time(5, 30))
    counts = (
        {
            "count_id": "DEMO_COUNT_BASELINE_20250106",
            "cafe_id": CAFE_ID,
            "counted_at": baseline_at,
            "recorded_at": baseline_at + timedelta(minutes=15),
            "counted_by": "DEMO_EMP_001",
            "status": "verified",
            "is_synthetic": True,
        },
        {
            "count_id": "DEMO_COUNT_PARTIAL_20250110",
            "cafe_id": CAFE_ID,
            "counted_at": partial_at,
            "recorded_at": partial_at + timedelta(minutes=15),
            "counted_by": "DEMO_EMP_002",
            "status": "verified",
            "is_synthetic": True,
        },
    )
    lines = tuple(
        {
            "count_line_id": f"DEMO_LINE_BASELINE_{ingredient_id}",
            "count_id": counts[0]["count_id"],
            "cafe_id": CAFE_ID,
            "ingredient_id": ingredient_id,
            "counted_quantity": quantity,
        }
        for ingredient_id, quantity in BASELINE_QUANTITIES.items()
    ) + tuple(
        {
            "count_line_id": f"DEMO_LINE_PARTIAL_{ingredient_id}",
            "count_id": counts[1]["count_id"],
            "cafe_id": CAFE_ID,
            "ingredient_id": ingredient_id,
            "counted_quantity": quantity,
        }
        for ingredient_id, quantity in PARTIAL_QUANTITIES.items()
    )
    movements = tuple(
        {
            "movement_id": f"DEMO_MOVEMENT_{suffix}",
            "cafe_id": CAFE_ID,
            "ingredient_id": ingredient_id,
            "movement_type": movement_type,
            "quantity_delta": Decimal(quantity_delta),
            "is_estimated": False,
            "source_type": source_type,
            "source_id": f"DEMO_SOURCE_{suffix}",
            "occurred_at": _local_datetime(cafe_timezone, day_offset, time(hour)),
            "recorded_at": _local_datetime(cafe_timezone, day_offset, time(hour, 5)),
            "recorded_by": "DEMO_EMP_002",
            "reason": reason,
        }
        for (
            suffix,
            ingredient_id,
            movement_type,
            quantity_delta,
            day_offset,
            hour,
            source_type,
            reason,
        ) in MOVEMENT_DEFINITIONS
    )
    return counts, lines, movements


def _build_sales_plan(
    cafe_timezone: ZoneInfo,
    products: tuple[MenuProduct, ...],
) -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    sales: list[dict[str, object]] = []
    sale_items: list[dict[str, object]] = []
    product_count = len(products)
    for day_offset in range(7):
        sale_date = WEEK_START + timedelta(days=day_offset)
        for sale_index, hour in enumerate(SALE_HOURS, start=1):
            sale_id = f"DEMO_SALE_{sale_date:%Y%m%d}_{sale_index:03d}"
            sold_at = _local_datetime(
                cafe_timezone,
                day_offset,
                time(hour, (day_offset * 11 + sale_index * 7) % 60),
            )
            product_indexes = [(day_offset * len(SALE_HOURS) + sale_index - 1) % product_count]
            if sale_index % 2 == 0:
                product_indexes.append((product_indexes[0] + 7) % product_count)
            if sale_index % 5 == 0:
                product_indexes.append((product_indexes[0] + 13) % product_count)

            total_cents = 0
            for item_index, product_index in enumerate(product_indexes, start=1):
                product = products[product_index]
                quantity = 2 if item_index == 1 and sale_index % 9 == 0 else 1
                line_total = quantity * product.price_cents
                total_cents += line_total
                sale_items.append(
                    {
                        "sale_item_id": f"DEMO_SALE_ITEM_{sale_date:%Y%m%d}_{sale_index:03d}_{item_index}",
                        "sale_id": sale_id,
                        "cafe_id": CAFE_ID,
                        "product_id": product.product_id,
                        "quantity": quantity,
                        "unit_price_cents": product.price_cents,
                        "line_total_cents": line_total,
                    }
                )
            sales.append(
                {
                    "sale_id": sale_id,
                    "cafe_id": CAFE_ID,
                    "source_id": f"DEMO_POS_{sale_date:%Y%m%d}_{sale_index:03d}",
                    "sold_at": sold_at,
                    "recorded_at": sold_at + timedelta(minutes=2 + sale_index % 4),
                    "status": "completed",
                    "total_cents": total_cents,
                }
            )
    return tuple(sales), tuple(sale_items)


def _build_staffing_plan(cafe_timezone: ZoneInfo) -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    created_at = _local_datetime(cafe_timezone, -1, time(12))
    employees = tuple(
        {
            "employee_id": employee_id,
            "cafe_id": CAFE_ID,
            "name": name,
            "role": role,
            "active": True,
            "created_at": created_at,
        }
        for employee_id, name, role in EMPLOYEE_DEFINITIONS
    )
    shifts: list[dict[str, object]] = []
    for employee_id, day_offsets, start_time, end_time in SHIFT_PATTERNS:
        for day_offset in day_offsets:
            shift_date = WEEK_START + timedelta(days=day_offset)
            shifts.append(
                {
                    "shift_id": f"DEMO_SHIFT_{employee_id[-3:]}_{shift_date:%Y%m%d}",
                    "cafe_id": CAFE_ID,
                    "employee_id": employee_id,
                    "start_at": _local_datetime(cafe_timezone, day_offset, start_time),
                    "end_at": _local_datetime(cafe_timezone, day_offset, end_time),
                    "status": "scheduled",
                    "created_at": created_at,
                }
            )
    return employees, tuple(shifts)


def build_demo_plan(cafe: Cafe, inventory_items: tuple[InventoryItem, ...], products: tuple[MenuProduct, ...]) -> DemoPlan:
    ingredient_ids = {item.ingredient_id for item in inventory_items}
    expected_ingredients = set(BASELINE_QUANTITIES) | {UNKNOWN_INGREDIENT_ID}
    if not expected_ingredients.issubset(ingredient_ids):
        missing = sorted(expected_ingredients - ingredient_ids)
        raise DemoSeedError(f"Missing inventory prerequisites: {', '.join(missing)}")
    active_products = tuple(sorted((product for product in products if product.active), key=lambda product: product.product_id))
    if len(active_products) != 18:
        raise DemoSeedError("CAFE001 must have exactly 18 active menu products")

    try:
        cafe_timezone = ZoneInfo(cafe.timezone)
    except ZoneInfoNotFoundError as error:
        raise DemoSeedError(f"Invalid café timezone: {cafe.timezone}") from error

    counts, count_lines, movements = _build_inventory_plan(cafe_timezone)
    sales, sale_items = _build_sales_plan(cafe_timezone, active_products)
    employees, shifts = _build_staffing_plan(cafe_timezone)
    return DemoPlan(
        counts=counts,
        count_lines=count_lines,
        movements=movements,
        sales=sales,
        sale_items=sale_items,
        employees=employees,
        shifts=shifts,
    )


def _assert_matches(record: object, expected: dict[str, object], label: str) -> None:
    if any(getattr(record, field) != value for field, value in expected.items()):
        raise DemoSeedError(f"Conflicting existing {label}")


def _seed_record(session: Session, model, primary_key: str, data: dict[str, object], label: str) -> bool:
    existing = session.get(model, data[primary_key])
    if existing is None:
        session.add(model(**data))
        return True
    _assert_matches(existing, data, label)
    return False


def _seed_counts(session: Session, plan: DemoPlan) -> tuple[int, int]:
    counts_created = 0
    lines_created = 0
    lines_by_count = {
        count["count_id"]: tuple(
            line for line in plan.count_lines if line["count_id"] == count["count_id"]
        )
        for count in plan.counts
    }
    for count_data in plan.counts:
        count_id = count_data["count_id"]
        existing_count = session.get(StockCount, count_id)
        expected_lines = lines_by_count[count_id]
        if existing_count is None:
            session.add(StockCount(**count_data))
            counts_created += 1
            for line_data in expected_lines:
                if session.get(StockCountLine, line_data["count_line_id"]) is not None:
                    raise DemoSeedError(f"Conflicting existing count line: {line_data['count_line_id']}")
                session.add(StockCountLine(**line_data))
                lines_created += 1
            continue

        _assert_matches(existing_count, count_data, f"stock count: {count_id}")
        existing_lines = {
            line.count_line_id: line
            for line in session.scalars(
                select(StockCountLine).where(StockCountLine.count_id == count_id)
            )
        }
        if set(existing_lines) != {line["count_line_id"] for line in expected_lines}:
            raise DemoSeedError(f"Conflicting existing count lines for: {count_id}")
        for line_data in expected_lines:
            _assert_matches(
                existing_lines[line_data["count_line_id"]],
                line_data,
                f"count line: {line_data['count_line_id']}",
            )
    return counts_created, lines_created


def _seed_movements(session: Session, plan: DemoPlan) -> int:
    created = 0
    for data in plan.movements:
        existing_by_identity = session.scalar(
            select(InventoryMovement).where(
                InventoryMovement.ingredient_id == data["ingredient_id"],
                InventoryMovement.source_type == data["source_type"],
                InventoryMovement.source_id == data["source_id"],
            )
        )
        existing_by_id = session.get(InventoryMovement, data["movement_id"])
        if existing_by_id is None and existing_by_identity is None:
            session.add(InventoryMovement(**data))
            created += 1
            continue
        if existing_by_id is None or existing_by_identity is None or existing_by_id is not existing_by_identity:
            raise DemoSeedError(f"Conflicting movement identity: {data['movement_id']}")
        _assert_matches(existing_by_id, data, f"movement: {data['movement_id']}")
    return created


def _seed_sales(session: Session, plan: DemoPlan) -> tuple[int, int]:
    sales_created = 0
    items_created = 0
    items_by_sale = {
        sale["sale_id"]: tuple(
            item for item in plan.sale_items if item["sale_id"] == sale["sale_id"]
        )
        for sale in plan.sales
    }
    for sale_data in plan.sales:
        sale_id = sale_data["sale_id"]
        existing_by_source = session.scalar(
            select(Sale).where(
                Sale.cafe_id == CAFE_ID,
                Sale.source_id == sale_data["source_id"],
            )
        )
        existing_sale = session.get(Sale, sale_id)
        expected_items = items_by_sale[sale_id]
        if existing_sale is None and existing_by_source is None:
            session.add(Sale(**sale_data))
            sales_created += 1
            for item_data in expected_items:
                if session.get(SaleItem, item_data["sale_item_id"]) is not None:
                    raise DemoSeedError(f"Conflicting existing sale item: {item_data['sale_item_id']}")
                session.add(SaleItem(**item_data))
                items_created += 1
            continue
        if existing_sale is None or existing_by_source is None or existing_sale is not existing_by_source:
            raise DemoSeedError(f"Conflicting sale identity: {sale_id}")

        _assert_matches(existing_sale, sale_data, f"sale: {sale_id}")
        existing_items = {
            item.sale_item_id: item
            for item in session.scalars(select(SaleItem).where(SaleItem.sale_id == sale_id))
        }
        if set(existing_items) != {item["sale_item_id"] for item in expected_items}:
            raise DemoSeedError(f"Conflicting existing sale items for: {sale_id}")
        for item_data in expected_items:
            _assert_matches(
                existing_items[item_data["sale_item_id"]],
                item_data,
                f"sale item: {item_data['sale_item_id']}",
            )
    return sales_created, items_created


def seed_demo_operations(session: Session) -> DemoSeedResult:
    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    with transaction:
        cafe = session.get(Cafe, CAFE_ID)
        if cafe is None:
            raise DemoSeedError(f"Café does not exist: {CAFE_ID}")
        inventory_items = tuple(
            session.scalars(
                select(InventoryItem).where(InventoryItem.cafe_id == CAFE_ID)
            )
        )
        products = tuple(
            session.scalars(select(MenuProduct).where(MenuProduct.cafe_id == CAFE_ID))
        )
        plan = build_demo_plan(cafe, inventory_items, products)

        counts_created, count_lines_created = _seed_counts(session, plan)
        movements_created = _seed_movements(session, plan)
        sales_created, sale_items_created = _seed_sales(session, plan)
        employees_created = sum(
            _seed_record(
                session,
                Employee,
                "employee_id",
                data,
                f"employee: {data['employee_id']}",
            )
            for data in plan.employees
        )
        session.flush()
        shifts_created = sum(
            _seed_record(
                session,
                Shift,
                "shift_id",
                data,
                f"shift: {data['shift_id']}",
            )
            for data in plan.shifts
        )
        session.flush()

    return DemoSeedResult(
        counts_created=counts_created,
        count_lines_created=count_lines_created,
        movements_created=movements_created,
        sales_created=sales_created,
        sale_items_created=sale_items_created,
        employees_created=employees_created,
        shifts_created=shifts_created,
    )


def main() -> None:
    with SessionLocal() as session:
        result = seed_demo_operations(session)
    print(
        "Demo operations seed completed: "
        f"{result.counts_created} counts, "
        f"{result.count_lines_created} count lines, "
        f"{result.movements_created} movements, "
        f"{result.sales_created} sales, "
        f"{result.sale_items_created} sale items, "
        f"{result.employees_created} employees, and "
        f"{result.shifts_created} shifts created."
    )


if __name__ == "__main__":
    main()
