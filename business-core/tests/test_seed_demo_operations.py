from collections import defaultdict
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.modules.inventory.models import (
    InventoryItem,
    InventoryMovement,
    StockCount,
    StockCountLine,
)
from backend.modules.sales.models import (
    MenuProduct,
    ProductVariation,
    Sale,
    SaleItem,
    SaleItemModifier,
)
from backend.modules.staffing.models import Employee, Shift
from scripts.seed_demo_operations import (
    CAFE_ID,
    DEMO_PREFIX,
    UNKNOWN_INGREDIENT_ID,
    DemoSeedError,
    DemoSeedResult,
    seed_demo_operations,
)
from scripts.seed_inventory import load_inventory_catalog, seed_inventory
from scripts.seed_sales import load_sales_catalog, seed_sales
from scripts.seed_sales_catalog import seed_sales_catalog


@pytest.fixture
def database_session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(database_session: Session) -> TestClient:
    app.dependency_overrides[get_db] = lambda: database_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def demo_seed_prerequisites(database_session: Session) -> None:
    inventory_catalog = load_inventory_catalog()
    sales_catalog = load_sales_catalog()

    assert inventory_catalog.cafe_id == CAFE_ID
    assert sales_catalog.cafe_id == CAFE_ID

    seed_inventory(database_session, inventory_catalog)
    seed_sales(database_session, sales_catalog)
    seed_sales_catalog(database_session, sales_catalog)

    assert all(
        database_session.get(InventoryItem, item.ingredient_id) is not None
        for item in inventory_catalog.items
    )
    assert all(
        database_session.get(MenuProduct, product.product_id) is not None
        for product in sales_catalog.products
    )


def _clear_demo_records(session: Session) -> None:
    session.execute(
        delete(SaleItem).where(SaleItem.sale_id.startswith(DEMO_PREFIX, autoescape=True))
    )
    session.execute(
        delete(Sale).where(Sale.sale_id.startswith(DEMO_PREFIX, autoescape=True))
    )
    session.execute(
        delete(InventoryMovement).where(
            InventoryMovement.movement_id.startswith(DEMO_PREFIX, autoescape=True)
        )
    )
    session.execute(
        delete(StockCountLine).where(
            StockCountLine.count_id.startswith(DEMO_PREFIX, autoescape=True)
        )
    )
    session.execute(
        delete(StockCount).where(
            StockCount.count_id.startswith(DEMO_PREFIX, autoescape=True)
        )
    )
    # Any shift for a demo employee blocks deleting that employee, including shifts
    # added by the Event Surge scenario, the app, or approved agent steps.
    session.execute(
        delete(Shift).where(
            Shift.shift_id.startswith(DEMO_PREFIX, autoescape=True)
            | Shift.employee_id.startswith(DEMO_PREFIX, autoescape=True)
        )
    )
    session.execute(
        delete(Employee).where(
            Employee.employee_id.startswith(DEMO_PREFIX, autoescape=True)
        )
    )
    session.flush()
    session.expire_all()


@pytest.mark.database
def test_demo_seed_creates_expected_records_and_is_idempotent(
    database_session: Session,
    demo_seed_prerequisites: None,
) -> None:
    _clear_demo_records(database_session)

    first = seed_demo_operations(database_session)
    second = seed_demo_operations(database_session)

    assert first == DemoSeedResult(
        counts_created=2,
        count_lines_created=23,
        movements_created=12,
        sales_created=126,
        sale_items_created=210,
        employees_created=7,
        shifts_created=35,
    )
    assert second == DemoSeedResult(0, 0, 0, 0, 0, 0, 0)
    assert database_session.scalar(
        select(func.count())
        .select_from(Sale)
        .where(Sale.sale_id.startswith(DEMO_PREFIX, autoescape=True))
    ) == 126
    assert database_session.scalar(
        select(func.count())
        .select_from(Shift)
        .where(Shift.shift_id.startswith(DEMO_PREFIX, autoescape=True))
    ) == 35


@pytest.mark.database
def test_demo_inventory_is_synthetic_chronological_and_preserves_unknown_stock(
    database_session: Session,
    client: TestClient,
    demo_seed_prerequisites: None,
) -> None:
    _clear_demo_records(database_session)
    seed_demo_operations(database_session)

    counts = list(
        database_session.scalars(
            select(StockCount)
            .where(StockCount.count_id.startswith(DEMO_PREFIX, autoescape=True))
            .order_by(StockCount.counted_at)
        )
    )
    lines = list(
        database_session.scalars(
            select(StockCountLine).where(
                StockCountLine.count_id.startswith(DEMO_PREFIX, autoescape=True)
            )
        )
    )
    movements = list(
        database_session.scalars(
            select(InventoryMovement).where(
                InventoryMovement.movement_id.startswith(DEMO_PREFIX, autoescape=True)
            )
        )
    )

    assert len(counts) == 2
    assert all(count.status == "verified" and count.is_synthetic for count in counts)
    assert len([line for line in lines if line.count_id == counts[0].count_id]) == 18
    assert len([line for line in lines if line.count_id == counts[1].count_id]) == 5
    assert UNKNOWN_INGREDIENT_ID not in {line.ingredient_id for line in lines}
    assert all(movement.occurred_at > counts[0].counted_at for movement in movements)
    assert {movement.movement_type for movement in movements} == {
        "RECEIPT",
        "WASTE",
        "ADJUSTMENT",
    }
    assert all(movement.source_id.startswith("DEMO_SOURCE_") for movement in movements)
    assert any(
        movement.ingredient_id == UNKNOWN_INGREDIENT_ID for movement in movements
    )

    stock = client.get(
        "/api/v1/inventory/stock",
        params={"cafe_id": CAFE_ID},
    )
    assert stock.status_code == 200
    by_ingredient = {item["ingredient_id"]: item for item in stock.json()}
    assert by_ingredient[UNKNOWN_INGREDIENT_ID]["stock_status"] == "unknown"
    assert by_ingredient["COFFEE_BEANS"]["stock_status"] == "known"
    assert by_ingredient["COFFEE_BEANS"]["latest_verified_count_id"] == (
        "DEMO_COUNT_PARTIAL_20250110"
    )


@pytest.mark.database
def test_demo_sales_use_database_prices_and_reports_are_meaningful(
    database_session: Session,
    client: TestClient,
    demo_seed_prerequisites: None,
) -> None:
    _clear_demo_records(database_session)
    seed_demo_operations(database_session)

    prices = {
        product.product_id: product.price_cents
        for product in database_session.scalars(
            select(MenuProduct).where(MenuProduct.cafe_id == CAFE_ID)
        )
    }
    regular_variations = {
        variation.product_id: variation
        for variation in database_session.scalars(
            select(ProductVariation).where(
                ProductVariation.cafe_id == CAFE_ID,
                ProductVariation.code == "REG",
            )
        )
    }
    sales = list(
        database_session.scalars(
            select(Sale).where(Sale.sale_id.startswith(DEMO_PREFIX, autoescape=True))
        )
    )
    items = list(
        database_session.scalars(
            select(SaleItem).where(
                SaleItem.sale_id.startswith(DEMO_PREFIX, autoescape=True)
            )
        )
    )
    items_by_sale: dict[str, list[SaleItem]] = defaultdict(list)
    for item in items:
        items_by_sale[item.sale_id].append(item)

    assert len(sales) == 126
    assert len(items) == 210
    assert all(
        sale.source == "generated"
        and sale.external_id == sale.source_id
        and sale.subtotal_cents == sale.total_cents
        and sale.discount_cents == 0
        and sale.tax_cents == 0
        for sale in sales
    )
    assert all(item.unit_price_cents == prices[item.product_id] for item in items)
    assert all(
        item.variation_id == regular_variations[item.product_id].variation_id
        and item.product_name
        == database_session.get(MenuProduct, item.product_id).name
        and item.variation_name == regular_variations[item.product_id].name
        and item.modifier_unit_cents == 0
        for item in items
    )
    assert all(
        sale.total_cents
        == sum(item.line_total_cents for item in items_by_sale[sale.sale_id])
        for sale in sales
    )
    assert {item.product_id for item in items} == set(prices)
    assert database_session.scalar(
        select(func.count())
        .select_from(SaleItemModifier)
        .where(SaleItemModifier.sale_item_id.startswith(DEMO_PREFIX, autoescape=True))
    ) == 0
    assert database_session.scalar(
        select(func.count())
        .select_from(InventoryMovement)
        .where(
            InventoryMovement.movement_id.startswith(DEMO_PREFIX, autoescape=True),
            InventoryMovement.movement_type == "SALE_USAGE",
        )
    ) == 0

    report = client.get(
        "/api/v1/sales/reports/summary",
        params={
            "cafe_id": CAFE_ID,
            "start_at": "2025-01-06T00:00:00-08:00",
            "end_at": "2025-01-13T00:00:00-08:00",
        },
    )
    assert report.status_code == 200
    assert report.json()["sales_count"] == 126
    assert report.json()["total_revenue_cents"] > 0
    assert len(report.json()["top_products"]) == 5
    assert len(report.json()["hourly_sales"]) >= 10


@pytest.mark.database
def test_demo_staffing_has_no_overlap_and_exposes_coverage_gaps(
    database_session: Session,
    client: TestClient,
    demo_seed_prerequisites: None,
) -> None:
    _clear_demo_records(database_session)
    seed_demo_operations(database_session)

    employees = list(
        database_session.scalars(
            select(Employee).where(
                Employee.employee_id.startswith(DEMO_PREFIX, autoescape=True)
            )
        )
    )
    shifts = list(
        database_session.scalars(
            select(Shift)
            .where(Shift.shift_id.startswith(DEMO_PREFIX, autoescape=True))
            .order_by(Shift.employee_id, Shift.start_at)
        )
    )
    shifts_by_employee: dict[str, list[Shift]] = defaultdict(list)
    for shift in shifts:
        shifts_by_employee[shift.employee_id].append(shift)

    assert len(employees) == 7
    assert len(shifts) == 35
    assert all(shift.status == "scheduled" for shift in shifts)
    assert all(
        earlier.end_at <= later.start_at
        for employee_shifts in shifts_by_employee.values()
        for earlier, later in zip(employee_shifts, employee_shifts[1:])
    )

    coverage = client.get(
        "/api/v1/staffing/reports/coverage",
        params={
            "cafe_id": CAFE_ID,
            "start_at": "2025-01-06T06:00:00-08:00",
            "end_at": "2025-01-06T22:00:00-08:00",
            "required_staff": 3,
        },
    )
    assert coverage.status_code == 200
    statuses = {hour["coverage_status"] for hour in coverage.json()["hours"]}
    assert statuses == {"adequate", "understaffed"}


@pytest.mark.database
def test_demo_seed_conflict_rolls_back_every_new_record(
    database_session: Session,
    demo_seed_prerequisites: None,
) -> None:
    _clear_demo_records(database_session)
    database_session.add(
        Employee(
            employee_id="DEMO_EMP_001",
            cafe_id=CAFE_ID,
            name="Conflicting Employee",
            role="barista",
            active=True,
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )
    )
    database_session.flush()

    with pytest.raises(DemoSeedError, match="Conflicting existing employee"):
        seed_demo_operations(database_session)

    assert database_session.scalar(
        select(func.count())
        .select_from(StockCount)
        .where(StockCount.count_id.startswith(DEMO_PREFIX, autoescape=True))
    ) == 0
    assert database_session.scalar(
        select(func.count())
        .select_from(Sale)
        .where(Sale.sale_id.startswith(DEMO_PREFIX, autoescape=True))
    ) == 0
    assert database_session.scalar(
        select(func.count())
        .select_from(Shift)
        .where(Shift.shift_id.startswith(DEMO_PREFIX, autoescape=True))
    ) == 0
