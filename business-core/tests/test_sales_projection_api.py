from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.modules.inventory.models import InventoryMovement
from backend.modules.sales.models import Sale
from scripts.seed_sales_catalog import catalog_identifier
from tests.sales_v2_support import seed_sales_v2_test_catalog

TEST_CAFE_ID = "TEST_PROJECTION_CAFE"
TARGET_AT = "2026-11-01T09:00:00-08:00"


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
    seed_sales_v2_test_catalog(database_session, TEST_CAFE_ID)
    app.dependency_overrides[get_db] = lambda: database_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def _id(local_id: str) -> str:
    return catalog_identifier(TEST_CAFE_ID, local_id)


def _counts(session: Session) -> tuple[int, int]:
    sales = session.scalar(select(func.count()).select_from(Sale).where(Sale.cafe_id == TEST_CAFE_ID))
    movements = session.scalar(
        select(func.count()).select_from(InventoryMovement).where(InventoryMovement.cafe_id == TEST_CAFE_ID)
    )
    return sales, movements


def test_projects_event_mix_through_recipes_and_oat_substitution(
    client: TestClient, database_session: Session
) -> None:
    before = _counts(database_session)
    response = client.post(
        "/api/v1/sales/projections/usage",
        json={
            "cafe_id": TEST_CAFE_ID,
            "target_at": TARGET_AT,
            "items": [
                {"variation_id": _id("COF003_REG"), "quantity": 40},
                {
                    "variation_id": _id("COF003_REG"),
                    "quantity": 20,
                    "modifiers": [{"modifier_id": _id("MILK_OAT"), "quantity": 1}],
                },
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["projection_status"] == "computed"
    assert body["is_estimate"] is True
    assert body["total_units"] == 60
    quantities = {line["ingredient_id"]: Decimal(line["quantity"]) for line in body["ingredients"]}
    assert quantities[_id("WHOLE_MILK")] == Decimal("9600")
    assert quantities[_id("OAT_MILK")] == Decimal("4800")
    assert quantities[_id("COFFEE_BEANS")] == Decimal("1080")
    assert _counts(database_session) == before


def test_projection_requires_timezone(client: TestClient) -> None:
    response = client.post(
        "/api/v1/sales/projections/usage",
        json={
            "cafe_id": TEST_CAFE_ID,
            "target_at": "2026-11-01T09:00:00",
            "items": [{"variation_id": _id("COF003_REG"), "quantity": 1}],
        },
    )
    assert response.status_code == 422


def test_projection_unknown_variation_is_not_found(client: TestClient) -> None:
    response = client.post(
        "/api/v1/sales/projections/usage",
        json={
            "cafe_id": TEST_CAFE_ID,
            "target_at": TARGET_AT,
            "items": [{"variation_id": "NOT_A_VARIATION", "quantity": 1}],
        },
    )
    assert response.status_code == 404


def test_projection_unknown_cafe_is_not_found(client: TestClient) -> None:
    response = client.post(
        "/api/v1/sales/projections/usage",
        json={
            "cafe_id": "NO_SUCH_CAFE",
            "target_at": TARGET_AT,
            "items": [{"variation_id": _id("COF003_REG"), "quantity": 1}],
        },
    )
    assert response.status_code == 404
