from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.inventory.models import (
    InventoryItem,
    InventoryMovement,
    StockCount,
    StockCountLine,
)

TEST_CAFE_ID = "TEST_STOCK_ESTIMATE_CAFE"


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
def stock_catalog(database_session: Session) -> None:
    database_session.add(Cafe(cafe_id=TEST_CAFE_ID, name="Stock Estimate Test", timezone="UTC"))
    database_session.add_all(
        [
            InventoryItem(ingredient_id="TEST_STOCK_OAT", cafe_id=TEST_CAFE_ID, name="Oat", category="dairy", base_unit="mL"),
            InventoryItem(ingredient_id="TEST_STOCK_BEANS", cafe_id=TEST_CAFE_ID, name="Beans", category="coffee", base_unit="g"),
            InventoryItem(ingredient_id="TEST_STOCK_TEA", cafe_id=TEST_CAFE_ID, name="Tea", category="tea", base_unit="each"),
        ]
    )
    database_session.flush()


def _at(day: int, hour: int = 0) -> datetime:
    return datetime(2025, 1, day, hour, tzinfo=timezone.utc)


def _count(
    session: Session,
    count_id: str,
    status: str,
    counted_at: datetime | None,
    lines: dict[str, str],
    *,
    recorded_at: datetime | None = None,
    is_synthetic: bool = False,
) -> None:
    session.add(
        StockCount(
            count_id=count_id,
            cafe_id=TEST_CAFE_ID,
            status=status,
            counted_at=counted_at,
            recorded_at=recorded_at or _at(1),
            is_synthetic=is_synthetic,
        )
    )
    session.add_all(
        [
            StockCountLine(
                count_line_id=f"line_{count_id}_{ingredient_id}",
                count_id=count_id,
                cafe_id=TEST_CAFE_ID,
                ingredient_id=ingredient_id,
                counted_quantity=Decimal(quantity),
            )
            for ingredient_id, quantity in lines.items()
        ]
    )
    session.flush()


def _movement(
    session: Session,
    movement_id: str,
    ingredient_id: str,
    quantity: str,
    occurred_at: datetime,
    *,
    recorded_at: datetime | None = None,
    is_estimated: bool = False,
    movement_type: str = "ADJUSTMENT",
) -> None:
    session.add(
        InventoryMovement(
            movement_id=movement_id,
            cafe_id=TEST_CAFE_ID,
            ingredient_id=ingredient_id,
            movement_type=movement_type,
            quantity_delta=Decimal(quantity),
            is_estimated=is_estimated,
            source_type="stock_estimate_test",
            source_id=movement_id,
            occurred_at=occurred_at,
            recorded_at=recorded_at or _at(20),
        )
    )
    session.flush()


@pytest.mark.database
def test_no_verified_count_is_unknown_even_when_movements_exist(
    client: TestClient, stock_catalog: None, database_session: Session
) -> None:
    _movement(database_session, "movement_unknown", "TEST_STOCK_OAT", "10", _at(2))

    response = client.get("/api/v1/inventory/stock/TEST_STOCK_OAT", params={"cafe_id": TEST_CAFE_ID})

    assert response.status_code == 200
    assert response.json()["stock_status"] == "unknown"
    assert response.json()["estimated_quantity"] is None
    assert response.json()["included_movement_count"] == 0


@pytest.mark.database
def test_draft_and_void_counts_are_ignored(
    client: TestClient, stock_catalog: None, database_session: Session
) -> None:
    _count(database_session, "draft_count", "draft", _at(2), {"TEST_STOCK_OAT": "5"})
    _count(database_session, "void_count", "void", _at(3), {"TEST_STOCK_OAT": "7"})

    response = client.get("/api/v1/inventory/stock/TEST_STOCK_OAT", params={"cafe_id": TEST_CAFE_ID})

    assert response.json()["stock_status"] == "unknown"


@pytest.mark.database
def test_verified_count_uses_only_later_signed_movements(
    client: TestClient, stock_catalog: None, database_session: Session
) -> None:
    _count(database_session, "anchor", "verified", _at(5), {"TEST_STOCK_OAT": "100"}, is_synthetic=True)
    _movement(database_session, "before", "TEST_STOCK_OAT", "20", _at(4), movement_type="RECEIPT")
    _movement(database_session, "at_anchor", "TEST_STOCK_OAT", "20", _at(5), movement_type="RECEIPT")
    _movement(database_session, "receipt", "TEST_STOCK_OAT", "20", _at(6), movement_type="RECEIPT")
    _movement(database_session, "waste", "TEST_STOCK_OAT", "-5", _at(7), movement_type="WASTE")
    _movement(database_session, "adjustment", "TEST_STOCK_OAT", "3", _at(8), is_estimated=True)
    _movement(database_session, "sale_usage", "TEST_STOCK_OAT", "-2", _at(9), movement_type="SALE_USAGE")
    _movement(database_session, "future", "TEST_STOCK_OAT", "100", datetime(2100, 1, 1, tzinfo=timezone.utc), movement_type="RECEIPT")

    response = client.get("/api/v1/inventory/stock/TEST_STOCK_OAT", params={"cafe_id": TEST_CAFE_ID})

    assert response.status_code == 200
    body = response.json()
    assert body["stock_status"] == "known"
    assert body["estimated_quantity"] == "116.000"
    assert body["movement_delta"] == "16.000"
    assert body["included_movement_count"] == 4
    assert body["latest_verified_count_id"] == "anchor"
    assert body["anchor_is_synthetic"] is True
    assert body["has_estimated_movements"] is True
    assert body["as_of"] == body["calculated_at"]


@pytest.mark.database
def test_partial_counts_use_per_ingredient_latest_anchor_and_tie_breakers(
    client: TestClient, stock_catalog: None, database_session: Session
) -> None:
    _count(database_session, "oat_old", "verified", _at(2), {"TEST_STOCK_OAT": "10"})
    _count(database_session, "beans_anchor", "verified", _at(4), {"TEST_STOCK_BEANS": "50"})
    _count(database_session, "oat_tie_a", "verified", _at(5), {"TEST_STOCK_OAT": "20"}, recorded_at=_at(5, 1))
    _count(database_session, "oat_tie_b", "verified", _at(5), {"TEST_STOCK_OAT": "30"}, recorded_at=_at(5, 2))
    _movement(database_session, "late_before_newer_count", "TEST_STOCK_OAT", "100", _at(4), movement_type="RECEIPT")
    _movement(database_session, "oat_after", "TEST_STOCK_OAT", "2", _at(6))
    _movement(database_session, "beans_after", "TEST_STOCK_BEANS", "5", _at(6))

    response = client.get("/api/v1/inventory/stock", params={"cafe_id": TEST_CAFE_ID})
    estimates = {estimate["ingredient_id"]: estimate for estimate in response.json()}

    assert estimates["TEST_STOCK_OAT"]["latest_verified_count_id"] == "oat_tie_b"
    assert estimates["TEST_STOCK_OAT"]["estimated_quantity"] == "32.000"
    assert estimates["TEST_STOCK_BEANS"]["latest_verified_count_id"] == "beans_anchor"
    assert estimates["TEST_STOCK_BEANS"]["estimated_quantity"] == "55.000"
    assert estimates["TEST_STOCK_TEA"]["stock_status"] == "unknown"


@pytest.mark.database
def test_zero_count_and_negative_estimate_are_reported_without_clamping(
    client: TestClient, stock_catalog: None, database_session: Session
) -> None:
    _count(database_session, "zero_anchor", "verified", _at(2), {"TEST_STOCK_TEA": "0"})
    _movement(database_session, "negative_after", "TEST_STOCK_TEA", "-1", _at(3))

    response = client.get("/api/v1/inventory/stock/TEST_STOCK_TEA", params={"cafe_id": TEST_CAFE_ID})

    body = response.json()
    assert body["stock_status"] == "inconsistent"
    assert body["estimated_quantity"] == "-1.000"
    assert "negative" in body["reason"]


@pytest.mark.database
def test_stock_routes_are_read_only_and_handle_unknown_resources(
    client: TestClient, stock_catalog: None, database_session: Session
) -> None:
    before_movements = database_session.scalar(select(func.count()).select_from(InventoryMovement))
    before_counts = database_session.scalar(select(func.count()).select_from(StockCount))
    unknown_cafe = client.get("/api/v1/inventory/stock", params={"cafe_id": "MISSING_CAFE"})
    unknown_item = client.get(
        "/api/v1/inventory/stock/MISSING_ITEM", params={"cafe_id": TEST_CAFE_ID}
    )
    catalog = client.get("/api/v1/inventory/items", params={"cafe_id": TEST_CAFE_ID})
    movements = client.get("/api/v1/inventory/movements", params={"cafe_id": TEST_CAFE_ID})
    counts = client.get("/api/v1/inventory/counts", params={"cafe_id": TEST_CAFE_ID})

    assert unknown_cafe.status_code == 404
    assert unknown_item.status_code == 404
    assert catalog.status_code == movements.status_code == counts.status_code == 200
    assert database_session.scalar(select(func.count()).select_from(InventoryMovement)) == before_movements
    assert database_session.scalar(select(func.count()).select_from(StockCount)) == before_counts


@pytest.mark.database
def test_as_of_selects_historical_anchor_and_excludes_late_recorded_count(
    client: TestClient, stock_catalog: None, database_session: Session
) -> None:
    _count(
        database_session,
        "historical_anchor",
        "verified",
        _at(2),
        {"TEST_STOCK_OAT": "40"},
        recorded_at=_at(2, 1),
    )
    _count(
        database_session,
        "late_newer_anchor",
        "verified",
        _at(4),
        {"TEST_STOCK_OAT": "80"},
        recorded_at=_at(7),
    )

    response = client.get(
        "/api/v1/inventory/stock/TEST_STOCK_OAT",
        params={"cafe_id": TEST_CAFE_ID, "as_of": _at(5).isoformat()},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["latest_verified_count_id"] == "historical_anchor"
    assert body["estimated_quantity"] == "40.000"
    assert body["as_of"] == _at(5).isoformat().replace("+00:00", "Z")
    calculated_at = datetime.fromisoformat(
        body["calculated_at"].replace("Z", "+00:00")
    )
    assert calculated_at > _at(5)


@pytest.mark.database
def test_as_of_excludes_late_recorded_and_future_movements(
    client: TestClient, stock_catalog: None, database_session: Session
) -> None:
    _count(
        database_session,
        "movement_anchor",
        "verified",
        _at(2),
        {"TEST_STOCK_OAT": "100"},
        recorded_at=_at(2, 1),
    )
    _movement(
        database_session,
        "known_receipt",
        "TEST_STOCK_OAT",
        "10",
        _at(3),
        recorded_at=_at(3, 1),
        movement_type="RECEIPT",
    )
    _movement(
        database_session,
        "late_receipt",
        "TEST_STOCK_OAT",
        "20",
        _at(4),
        recorded_at=_at(8),
        movement_type="RECEIPT",
    )
    _movement(
        database_session,
        "future_receipt",
        "TEST_STOCK_OAT",
        "30",
        _at(7),
        recorded_at=_at(4),
        movement_type="RECEIPT",
    )

    early = client.get(
        "/api/v1/inventory/stock/TEST_STOCK_OAT",
        params={"cafe_id": TEST_CAFE_ID, "as_of": _at(5).isoformat()},
    )
    later = client.get(
        "/api/v1/inventory/stock/TEST_STOCK_OAT",
        params={"cafe_id": TEST_CAFE_ID, "as_of": _at(9).isoformat()},
    )

    assert early.json()["estimated_quantity"] == "110.000"
    assert early.json()["included_movement_count"] == 1
    assert later.json()["estimated_quantity"] == "160.000"
    assert later.json()["included_movement_count"] == 3


@pytest.mark.database
def test_as_of_list_and_item_share_timezone_aware_semantics(
    client: TestClient, stock_catalog: None, database_session: Session
) -> None:
    _count(
        database_session,
        "offset_anchor",
        "verified",
        datetime(2025, 1, 4, 7, tzinfo=timezone.utc),
        {"TEST_STOCK_TEA": "0"},
        recorded_at=datetime(2025, 1, 4, 7, 30, tzinfo=timezone.utc),
    )
    _movement(
        database_session,
        "offset_negative",
        "TEST_STOCK_TEA",
        "-1",
        datetime(2025, 1, 4, 8, tzinfo=timezone.utc),
        recorded_at=datetime(2025, 1, 4, 8, tzinfo=timezone.utc),
    )
    as_of = "2025-01-04T00:00:00-08:00"

    list_response = client.get(
        "/api/v1/inventory/stock",
        params={"cafe_id": TEST_CAFE_ID, "as_of": as_of},
    )
    item_response = client.get(
        "/api/v1/inventory/stock/TEST_STOCK_TEA",
        params={"cafe_id": TEST_CAFE_ID, "as_of": as_of},
    )

    estimates = {
        estimate["ingredient_id"]: estimate for estimate in list_response.json()
    }
    item = item_response.json()
    assert list_response.status_code == item_response.status_code == 200
    list_item = estimates["TEST_STOCK_TEA"]
    assert {
        key: value for key, value in list_item.items() if key != "calculated_at"
    } == {key: value for key, value in item.items() if key != "calculated_at"}
    assert item["stock_status"] == "inconsistent"
    assert item["estimated_quantity"] == "-1.000"
    assert estimates["TEST_STOCK_OAT"]["stock_status"] == "unknown"
    assert item["as_of"] == as_of


@pytest.mark.database
def test_as_of_rejects_naive_and_malformed_timestamps(
    client: TestClient, stock_catalog: None
) -> None:
    naive = client.get(
        "/api/v1/inventory/stock",
        params={"cafe_id": TEST_CAFE_ID, "as_of": "2025-01-05T12:00:00"},
    )
    malformed = client.get(
        "/api/v1/inventory/stock/TEST_STOCK_OAT",
        params={"cafe_id": TEST_CAFE_ID, "as_of": "not-a-timestamp"},
    )

    assert naive.status_code == 400
    assert naive.json()["detail"] == "as_of must include a timezone"
    assert malformed.status_code == 422


@pytest.mark.database
def test_january_demo_as_of_is_read_only_and_preserves_records(
    client: TestClient, database_session: Session
) -> None:
    counts_before = database_session.scalar(
        select(func.count()).select_from(StockCount).where(
            StockCount.cafe_id == "CAFE001"
        )
    )
    movements_before = database_session.scalar(
        select(func.count()).select_from(InventoryMovement).where(
            InventoryMovement.cafe_id == "CAFE001"
        )
    )

    response = client.get(
        "/api/v1/inventory/stock",
        params={
            "cafe_id": "CAFE001",
            "as_of": "2025-01-07T23:59:59-08:00",
        },
    )

    assert response.status_code == 200
    estimates = {item["ingredient_id"]: item for item in response.json()}
    assert len(estimates) == 19
    assert estimates["COFFEE_BEANS"]["latest_verified_count_id"] == (
        "DEMO_COUNT_BASELINE_20250106"
    )
    assert estimates["TEA_BAG"]["stock_status"] == "unknown"
    assert database_session.scalar(
        select(func.count()).select_from(StockCount).where(
            StockCount.cafe_id == "CAFE001"
        )
    ) == counts_before
    assert database_session.scalar(
        select(func.count()).select_from(InventoryMovement).where(
            InventoryMovement.cafe_id == "CAFE001"
        )
    ) == movements_before
