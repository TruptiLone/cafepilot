import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.inventory.models import InventoryItem

TEST_CAFE_ID = "TEST_INVENTORY_API_CAFE"


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
def catalog(database_session: Session) -> None:
    database_session.add(Cafe(cafe_id=TEST_CAFE_ID, name="Inventory API Test", timezone="UTC"))
    database_session.add_all(
        [
            InventoryItem(
                ingredient_id="TEST_API_ACTIVE_A",
                cafe_id=TEST_CAFE_ID,
                name="Alpha Beans",
                category="coffee",
                base_unit="g",
            ),
            InventoryItem(
                ingredient_id="TEST_API_ACTIVE_B",
                cafe_id=TEST_CAFE_ID,
                name="Bravo Milk",
                category="dairy",
                base_unit="mL",
            ),
            InventoryItem(
                ingredient_id="TEST_API_INACTIVE",
                cafe_id=TEST_CAFE_ID,
                name="Retired Syrup",
                category="syrup",
                base_unit="mL",
                active=False,
            ),
        ]
    )
    database_session.flush()


@pytest.mark.database
def test_list_items_returns_active_items_in_deterministic_order(
    client: TestClient,
    catalog: None,
) -> None:
    response = client.get("/api/v1/inventory/items", params={"cafe_id": TEST_CAFE_ID})

    assert response.status_code == 200
    assert [item["ingredient_id"] for item in response.json()] == [
        "TEST_API_ACTIVE_A",
        "TEST_API_ACTIVE_B",
    ]


@pytest.mark.database
def test_get_known_item_returns_validated_catalog_response(
    client: TestClient,
    catalog: None,
) -> None:
    response = client.get(
        "/api/v1/inventory/items/TEST_API_ACTIVE_A",
        params={"cafe_id": TEST_CAFE_ID},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ingredient_id": "TEST_API_ACTIVE_A",
        "cafe_id": TEST_CAFE_ID,
        "name": "Alpha Beans",
        "category": "coffee",
        "base_unit": "g",
        "reorder_point": None,
        "par_level": None,
        "is_perishable": False,
        "active": True,
    }


@pytest.mark.database
def test_catalog_filters_category_and_can_include_inactive_items(
    client: TestClient,
    catalog: None,
) -> None:
    category_response = client.get(
        "/api/v1/inventory/items",
        params={"cafe_id": TEST_CAFE_ID, "category": "dairy"},
    )
    inactive_response = client.get(
        "/api/v1/inventory/items",
        params={"cafe_id": TEST_CAFE_ID, "include_inactive": "true"},
    )

    assert [item["ingredient_id"] for item in category_response.json()] == [
        "TEST_API_ACTIVE_B"
    ]
    assert [item["ingredient_id"] for item in inactive_response.json()] == [
        "TEST_API_ACTIVE_A",
        "TEST_API_ACTIVE_B",
        "TEST_API_INACTIVE",
    ]


@pytest.mark.database
def test_catalog_returns_an_empty_list_when_no_items_match(
    client: TestClient,
    catalog: None,
) -> None:
    response = client.get(
        "/api/v1/inventory/items",
        params={"cafe_id": TEST_CAFE_ID, "category": "missing-category"},
    )

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.database
def test_catalog_returns_not_found_for_unknown_cafe_or_item(
    client: TestClient,
    catalog: None,
) -> None:
    unknown_cafe = client.get(
        "/api/v1/inventory/items", params={"cafe_id": "MISSING_CAFE"}
    )
    unknown_item = client.get(
        "/api/v1/inventory/items/MISSING_ITEM",
        params={"cafe_id": TEST_CAFE_ID},
    )

    assert unknown_cafe.status_code == 404
    assert unknown_cafe.json()["detail"] == "Cafe not found"
    assert unknown_item.status_code == 404
    assert unknown_item.json()["detail"] == "Inventory item not found"


@pytest.mark.database
def test_catalog_requests_do_not_modify_records(
    client: TestClient,
    catalog: None,
    database_session: Session,
) -> None:
    before = database_session.scalar(
        select(func.count()).select_from(InventoryItem).where(
            InventoryItem.cafe_id == TEST_CAFE_ID
        )
    )

    response = client.get("/api/v1/inventory/items", params={"cafe_id": TEST_CAFE_ID})

    after = database_session.scalar(
        select(func.count()).select_from(InventoryItem).where(
            InventoryItem.cafe_id == TEST_CAFE_ID
        )
    )
    assert response.status_code == 200
    assert before == after == 3


def test_existing_health_endpoints_are_available() -> None:
    with TestClient(app) as client:
        assert client.get("/health").json() == {
            "status": "ok",
            "application": "cafe-business-core",
        }
        assert client.get("/api/v1/inventory/health").json() == {
            "status": "ok",
            "module": "inventory",
        }
