from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.sales.models import MenuProduct, ProductVariation, Sale, SaleItem

TEST_CAFE_ID = "TEST_SALES_API_CAFE"
OTHER_CAFE_ID = "TEST_SALES_API_OTHER_CAFE"


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
def sales_catalog(database_session: Session) -> None:
    database_session.add_all(
        [
            Cafe(
                cafe_id=TEST_CAFE_ID,
                name="Sales API Test",
                timezone="America/Los_Angeles",
            ),
            Cafe(cafe_id=OTHER_CAFE_ID, name="Other Sales API Test", timezone="UTC"),
            MenuProduct(
                product_id="TEST_SALES_COFFEE",
                cafe_id=TEST_CAFE_ID,
                name="Test Coffee",
                category="coffee",
                price_cents=500,
            ),
            MenuProduct(
                product_id="TEST_SALES_TEA",
                cafe_id=TEST_CAFE_ID,
                name="Test Tea",
                category="drinks",
                price_cents=300,
            ),
            MenuProduct(
                product_id="TEST_SALES_MUFFIN",
                cafe_id=TEST_CAFE_ID,
                name="Test Muffin",
                category="bakery",
                price_cents=400,
            ),
            MenuProduct(
                product_id="TEST_SALES_INACTIVE",
                cafe_id=TEST_CAFE_ID,
                name="Inactive Product",
                category="test",
                price_cents=100,
                active=False,
            ),
            MenuProduct(
                product_id="TEST_SALES_OTHER",
                cafe_id=OTHER_CAFE_ID,
                name="Other Product",
                category="test",
                price_cents=999,
            ),
        ]
    )
    database_session.flush()
    database_session.add_all(
        [
            ProductVariation(
                variation_id="TEST_SALES_COFFEE_REG",
                cafe_id=TEST_CAFE_ID,
                product_id="TEST_SALES_COFFEE",
                code="REG",
                name="Regular",
                size_oz=None,
                price_cents=500,
            ),
            ProductVariation(
                variation_id="TEST_SALES_TEA_REG",
                cafe_id=TEST_CAFE_ID,
                product_id="TEST_SALES_TEA",
                code="REG",
                name="Regular",
                size_oz=None,
                price_cents=300,
            ),
            ProductVariation(
                variation_id="TEST_SALES_MUFFIN_REG",
                cafe_id=TEST_CAFE_ID,
                product_id="TEST_SALES_MUFFIN",
                code="REG",
                name="Regular",
                size_oz=None,
                price_cents=400,
            ),
            ProductVariation(
                variation_id="TEST_SALES_OTHER_REG",
                cafe_id=OTHER_CAFE_ID,
                product_id="TEST_SALES_OTHER",
                code="REG",
                name="Regular",
                size_oz=None,
                price_cents=999,
            ),
        ]
    )
    database_session.flush()


def _sale_payload(**overrides) -> dict:
    payload = {
        "cafe_id": TEST_CAFE_ID,
        "source_id": "TEST_POS_001",
        "sold_at": "2025-01-02T08:30:00-08:00",
        "items": [
            {"product_id": "TEST_SALES_COFFEE", "quantity": 2},
            {"product_id": "TEST_SALES_MUFFIN", "quantity": 1},
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.database
def test_product_catalog_is_active_cafe_scoped_and_deterministic(
    client: TestClient,
    sales_catalog: None,
) -> None:
    response = client.get("/api/v1/sales/products", params={"cafe_id": TEST_CAFE_ID})

    assert response.status_code == 200
    assert [product["product_id"] for product in response.json()] == [
        "TEST_SALES_MUFFIN",
        "TEST_SALES_COFFEE",
        "TEST_SALES_TEA",
    ]
    assert client.get(
        "/api/v1/sales/products", params={"cafe_id": "UNKNOWN_CAFE"}
    ).status_code == 404


@pytest.mark.database
def test_sale_uses_server_totals_and_preserves_historical_prices(
    client: TestClient,
    sales_catalog: None,
    database_session: Session,
) -> None:
    response = client.post("/api/v1/sales", json=_sale_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["total_cents"] == 1400
    assert {
        item["product_id"]: (item["unit_price_cents"], item["line_total_cents"])
        for item in body["items"]
    } == {
        "TEST_SALES_COFFEE": (500, 1000),
        "TEST_SALES_MUFFIN": (400, 400),
    }
    stored_sale = database_session.get(Sale, body["sale_id"])
    assert (
        stored_sale.source,
        stored_sale.external_id,
        stored_sale.subtotal_cents,
        stored_sale.discount_cents,
        stored_sale.tax_cents,
    ) == ("pos", None, 1400, 0, 0)
    stored_items = list(
        database_session.scalars(
            select(SaleItem).where(SaleItem.sale_id == body["sale_id"])
        )
    )
    assert {
        item.product_id: (
            item.variation_id,
            item.product_name,
            item.variation_name,
            item.modifier_unit_cents,
        )
        for item in stored_items
    } == {
        "TEST_SALES_COFFEE": (
            "TEST_SALES_COFFEE_REG",
            "Test Coffee",
            "Regular",
            0,
        ),
        "TEST_SALES_MUFFIN": (
            "TEST_SALES_MUFFIN_REG",
            "Test Muffin",
            "Regular",
            0,
        ),
    }

    database_session.get(MenuProduct, "TEST_SALES_COFFEE").price_cents = 750
    database_session.get(MenuProduct, "TEST_SALES_COFFEE").name = "Renamed Coffee"
    database_session.flush()
    detail = client.get(
        f"/api/v1/sales/{body['sale_id']}",
        params={"cafe_id": TEST_CAFE_ID},
    )

    assert detail.status_code == 200
    coffee = next(
        item for item in detail.json()["items"] if item["product_id"] == "TEST_SALES_COFFEE"
    )
    assert coffee["unit_price_cents"] == 500
    assert coffee["product_name"] == "Test Coffee"


@pytest.mark.database
@pytest.mark.parametrize(
    "payload",
    [
        _sale_payload(items=[]),
        _sale_payload(items=[{"product_id": "TEST_SALES_COFFEE", "quantity": 0}]),
        _sale_payload(items=[{"product_id": "TEST_SALES_COFFEE", "quantity": 1.5}]),
        _sale_payload(
            items=[
                {"product_id": "TEST_SALES_COFFEE", "quantity": 1},
                {"product_id": "TEST_SALES_COFFEE", "quantity": 2},
            ]
        ),
        _sale_payload(sold_at="2025-01-02T08:30:00"),
        _sale_payload(sold_at="2100-01-02T08:30:00+00:00"),
        _sale_payload(source_id=" "),
        _sale_payload(total_cents=1),
        _sale_payload(
            items=[{"product_id": "TEST_SALES_COFFEE", "quantity": 5_000_000}]
        ),
    ],
)
def test_invalid_requests_leave_no_partial_sale(
    client: TestClient,
    sales_catalog: None,
    database_session: Session,
    payload: dict,
) -> None:
    response = client.post("/api/v1/sales", json=payload)

    assert response.status_code == 422
    assert database_session.scalar(
        select(func.count()).select_from(Sale).where(Sale.cafe_id == TEST_CAFE_ID)
    ) == 0
    assert database_session.scalar(
        select(func.count())
        .select_from(SaleItem)
        .where(SaleItem.cafe_id == TEST_CAFE_ID)
    ) == 0


@pytest.mark.database
def test_unknown_and_cross_cafe_products_are_rejected_atomically(
    client: TestClient,
    sales_catalog: None,
    database_session: Session,
) -> None:
    for product_id in ("UNKNOWN_PRODUCT", "TEST_SALES_OTHER", "TEST_SALES_INACTIVE"):
        response = client.post(
            "/api/v1/sales",
            json=_sale_payload(
                source_id=f"SOURCE_{product_id}",
                items=[
                    {"product_id": "TEST_SALES_COFFEE", "quantity": 1},
                    {"product_id": product_id, "quantity": 1},
                ],
            ),
        )
        assert response.status_code == 404

    assert client.post(
        "/api/v1/sales",
        json=_sale_payload(cafe_id="UNKNOWN_CAFE"),
    ).status_code == 404
    assert database_session.scalar(
        select(func.count()).select_from(Sale).where(Sale.cafe_id == TEST_CAFE_ID)
    ) == 0


@pytest.mark.database
def test_source_id_retry_returns_identical_sale_and_conflicts_on_changed_data(
    client: TestClient,
    sales_catalog: None,
    database_session: Session,
) -> None:
    first = client.post("/api/v1/sales", json=_sale_payload())
    retry = client.post("/api/v1/sales", json=_sale_payload())
    conflict = client.post(
        "/api/v1/sales",
        json=_sale_payload(
            items=[{"product_id": "TEST_SALES_COFFEE", "quantity": 1}]
        ),
    )

    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json() == first.json()
    assert conflict.status_code == 409
    assert database_session.scalar(
        select(func.count()).select_from(Sale).where(Sale.cafe_id == TEST_CAFE_ID)
    ) == 1
    assert database_session.scalar(
        select(func.count())
        .select_from(SaleItem)
        .where(SaleItem.cafe_id == TEST_CAFE_ID)
    ) == 2


@pytest.mark.database
def test_sale_detail_and_history_enforce_cafe_isolation(
    client: TestClient,
    sales_catalog: None,
) -> None:
    sale = client.post("/api/v1/sales", json=_sale_payload()).json()

    assert client.get(
        f"/api/v1/sales/{sale['sale_id']}", params={"cafe_id": OTHER_CAFE_ID}
    ).status_code == 404
    assert client.get(
        "/api/v1/sales/unknown", params={"cafe_id": TEST_CAFE_ID}
    ).status_code == 404
    history = client.get("/api/v1/sales", params={"cafe_id": TEST_CAFE_ID})
    assert history.status_code == 200
    assert [item["sale_id"] for item in history.json()] == [sale["sale_id"]]
    assert client.get("/api/v1/sales", params={"cafe_id": OTHER_CAFE_ID}).json() == []


@pytest.mark.database
def test_history_filters_and_report_use_cafe_timezone_and_completed_sales_only(
    client: TestClient,
    sales_catalog: None,
    database_session: Session,
) -> None:
    first = client.post("/api/v1/sales", json=_sale_payload())
    second = client.post(
        "/api/v1/sales",
        json=_sale_payload(
            source_id="TEST_POS_002",
            sold_at="2025-01-02T09:15:00-08:00",
            items=[
                {"product_id": "TEST_SALES_COFFEE", "quantity": 1},
                {"product_id": "TEST_SALES_TEA", "quantity": 3},
            ],
        ),
    )
    assert first.status_code == 201
    assert second.status_code == 201

    void_sale = Sale(
        sale_id="TEST_VOID_SALE",
        cafe_id=TEST_CAFE_ID,
        source_id="TEST_VOID_SOURCE",
        sold_at=datetime(2025, 1, 2, 18, tzinfo=timezone.utc),
        recorded_at=datetime(2025, 1, 2, 18, tzinfo=timezone.utc),
        status="void",
        source="pos",
        external_id=None,
        subtotal_cents=500,
        discount_cents=0,
        tax_cents=0,
        total_cents=500,
    )
    database_session.add(void_sale)
    database_session.add(
        SaleItem(
            sale_item_id="TEST_VOID_ITEM",
            sale_id=void_sale.sale_id,
            cafe_id=TEST_CAFE_ID,
            product_id="TEST_SALES_COFFEE",
            variation_id="TEST_SALES_COFFEE_REG",
            product_name="Test Coffee",
            variation_name="Regular",
            quantity=1,
            unit_price_cents=500,
            modifier_unit_cents=0,
            line_total_cents=500,
        )
    )
    database_session.flush()

    history = client.get(
        "/api/v1/sales",
        params={
            "cafe_id": TEST_CAFE_ID,
            "start_at": "2025-01-02T08:45:00-08:00",
            "end_at": "2025-01-02T10:00:00-08:00",
        },
    )
    assert history.status_code == 200
    assert [sale["source_id"] for sale in history.json()] == ["TEST_POS_002"]

    report = client.get(
        "/api/v1/sales/reports/summary",
        params={"cafe_id": TEST_CAFE_ID},
    )
    assert report.status_code == 200
    assert report.json() == {
        "cafe_id": TEST_CAFE_ID,
        "start_at": None,
        "end_at": None,
        "total_revenue_cents": 2800,
        "sales_count": 2,
        "total_units_sold": 7,
        "average_sale_value_cents": 1400,
        "top_products": [
            {
                "product_id": "TEST_SALES_COFFEE",
                "name": "Test Coffee",
                "quantity": 3,
                "revenue_cents": 1500,
            },
            {
                "product_id": "TEST_SALES_TEA",
                "name": "Test Tea",
                "quantity": 3,
                "revenue_cents": 900,
            },
            {
                "product_id": "TEST_SALES_MUFFIN",
                "name": "Test Muffin",
                "quantity": 1,
                "revenue_cents": 400,
            },
        ],
        "hourly_sales": [
            {"hour": 8, "sales_count": 1, "units_sold": 3, "revenue_cents": 1400},
            {"hour": 9, "sales_count": 1, "units_sold": 4, "revenue_cents": 1400},
        ],
    }


@pytest.mark.database
def test_empty_history_and_report_are_sensible(
    client: TestClient,
    sales_catalog: None,
) -> None:
    assert client.get("/api/v1/sales", params={"cafe_id": TEST_CAFE_ID}).json() == []
    report = client.get(
        "/api/v1/sales/reports/summary", params={"cafe_id": TEST_CAFE_ID}
    )

    assert report.status_code == 200
    assert report.json()["total_revenue_cents"] == 0
    assert report.json()["sales_count"] == 0
    assert report.json()["total_units_sold"] == 0
    assert report.json()["average_sale_value_cents"] == 0
    assert report.json()["top_products"] == []
    assert report.json()["hourly_sales"] == []


@pytest.mark.database
def test_invalid_date_range_is_rejected(
    client: TestClient,
    sales_catalog: None,
) -> None:
    response = client.get(
        "/api/v1/sales/reports/summary",
        params={
            "cafe_id": TEST_CAFE_ID,
            "start_at": "2025-01-03T00:00:00+00:00",
            "end_at": "2025-01-02T00:00:00+00:00",
        },
    )

    assert response.status_code == 400
