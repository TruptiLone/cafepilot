from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.inventory.models import InventoryItem, InventoryMovement, StockCount

TEST_CAFE_ID = "TEST_COUNT_API_CAFE"
OTHER_CAFE_ID = "TEST_COUNT_API_OTHER_CAFE"


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
def count_catalog(database_session: Session) -> None:
    database_session.add_all(
        [
            Cafe(cafe_id=TEST_CAFE_ID, name="Count API Test", timezone="UTC"),
            Cafe(cafe_id=OTHER_CAFE_ID, name="Other Count API Test", timezone="UTC"),
            InventoryItem(
                ingredient_id="TEST_COUNT_OAT",
                cafe_id=TEST_CAFE_ID,
                name="Test Oat Milk",
                category="dairy",
                base_unit="mL",
            ),
            InventoryItem(
                ingredient_id="TEST_COUNT_BEANS",
                cafe_id=TEST_CAFE_ID,
                name="Test Beans",
                category="coffee",
                base_unit="g",
            ),
            InventoryItem(
                ingredient_id="TEST_COUNT_UNCOUNTED",
                cafe_id=TEST_CAFE_ID,
                name="Test Uncounted Item",
                category="tea",
                base_unit="each",
            ),
            InventoryItem(
                ingredient_id="TEST_COUNT_OTHER_ITEM",
                cafe_id=OTHER_CAFE_ID,
                name="Other Item",
                category="coffee",
                base_unit="g",
            ),
        ]
    )
    database_session.flush()


def _draft_payload(**overrides) -> dict:
    payload = {
        "cafe_id": TEST_CAFE_ID,
        "status": "draft",
        "counted_by": "EMP_TEST",
        "is_synthetic": True,
        "items": [
            {"ingredient_id": "TEST_COUNT_OAT", "counted_quantity": "0"},
            {"ingredient_id": "TEST_COUNT_BEANS", "counted_quantity": "125.125"},
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.database
def test_create_draft_count_is_atomic_and_does_not_create_movements(
    client: TestClient, count_catalog: None, database_session: Session
) -> None:
    response = client.post("/api/v1/inventory/counts", json=_draft_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["counted_at"] is None
    assert body["counted_by"] == "EMP_TEST"
    assert body["is_synthetic"] is True
    assert [line["ingredient_id"] for line in body["lines"]] == [
        "TEST_COUNT_BEANS",
        "TEST_COUNT_OAT",
    ]
    assert next(
        line["counted_quantity"]
        for line in body["lines"]
        if line["ingredient_id"] == "TEST_COUNT_OAT"
    ) == "0"
    assert database_session.scalar(select(func.count()).select_from(InventoryMovement)) == 0


@pytest.mark.database
def test_create_verified_count_with_an_observation_timestamp(
    client: TestClient, count_catalog: None
) -> None:
    response = client.post(
        "/api/v1/inventory/counts",
        json=_draft_payload(status="verified", counted_at="2025-01-02T09:00:00-08:00"),
    )

    assert response.status_code == 201
    assert response.json()["status"] == "verified"
    assert datetime.fromisoformat(response.json()["counted_at"]) == datetime.fromisoformat(
        "2025-01-02T09:00:00-08:00"
    )


@pytest.mark.database
def test_draft_verification_requires_or_accepts_an_observation_timestamp(
    client: TestClient, count_catalog: None
) -> None:
    created = client.post("/api/v1/inventory/counts", json=_draft_payload()).json()

    missing_timestamp = client.post(
        f"/api/v1/inventory/counts/{created['count_id']}/verify",
        params={"cafe_id": TEST_CAFE_ID},
        json={},
    )
    verified = client.post(
        f"/api/v1/inventory/counts/{created['count_id']}/verify",
        params={"cafe_id": TEST_CAFE_ID},
        json={"counted_at": "2025-01-03T09:00:00+00:00"},
    )
    repeated = client.post(
        f"/api/v1/inventory/counts/{created['count_id']}/verify",
        params={"cafe_id": TEST_CAFE_ID},
        json={},
    )

    assert missing_timestamp.status_code == 400
    assert verified.status_code == 200
    assert verified.json()["status"] == "verified"
    assert repeated.status_code == 409


@pytest.mark.database
@pytest.mark.parametrize(
    "payload",
    [
        _draft_payload(counted_at="2025-01-02T09:00:00"),
        _draft_payload(counted_at="2100-01-02T09:00:00+00:00"),
        _draft_payload(items=[{"ingredient_id": "TEST_COUNT_OAT", "counted_quantity": "-1"}]),
        _draft_payload(items=[{"ingredient_id": "TEST_COUNT_OAT", "counted_quantity": "NaN"}]),
        _draft_payload(items=[{"ingredient_id": "TEST_COUNT_OAT", "counted_quantity": "1.0001"}]),
    ],
)
def test_invalid_timestamps_and_quantities_are_rejected_without_writes(
    client: TestClient, count_catalog: None, database_session: Session, payload: dict
) -> None:
    before = database_session.scalar(select(func.count()).select_from(StockCount))

    response = client.post("/api/v1/inventory/counts", json=payload)

    assert response.status_code == 422
    assert database_session.scalar(select(func.count()).select_from(StockCount)) == before


@pytest.mark.database
def test_unknown_and_cross_cafe_ingredients_fail_without_partial_counts(
    client: TestClient, count_catalog: None, database_session: Session
) -> None:
    before = database_session.scalar(select(func.count()).select_from(StockCount))
    unknown_cafe = client.post(
        "/api/v1/inventory/counts", json=_draft_payload(cafe_id="UNKNOWN_CAFE")
    )
    unknown_item = client.post(
        "/api/v1/inventory/counts",
        json=_draft_payload(items=[{"ingredient_id": "UNKNOWN_ITEM", "counted_quantity": 1}]),
    )
    cross_cafe = client.post(
        "/api/v1/inventory/counts",
        json=_draft_payload(items=[{"ingredient_id": "TEST_COUNT_OTHER_ITEM", "counted_quantity": 1}]),
    )

    assert [response.status_code for response in (unknown_cafe, unknown_item, cross_cafe)] == [404, 404, 404]
    assert database_session.scalar(select(func.count()).select_from(StockCount)) == before


@pytest.mark.database
def test_duplicate_lines_and_missing_verified_timestamp_are_rejected(
    client: TestClient, count_catalog: None
) -> None:
    duplicate = client.post(
        "/api/v1/inventory/counts",
        json=_draft_payload(
            items=[
                {"ingredient_id": "TEST_COUNT_OAT", "counted_quantity": 1},
                {"ingredient_id": "TEST_COUNT_OAT", "counted_quantity": 2},
            ]
        ),
    )
    missing_timestamp = client.post(
        "/api/v1/inventory/counts", json=_draft_payload(status="verified")
    )

    assert duplicate.status_code == 422
    assert missing_timestamp.status_code == 422


@pytest.mark.database
def test_count_history_detail_and_existing_catalog_are_readable(
    client: TestClient, count_catalog: None
) -> None:
    first = client.post(
        "/api/v1/inventory/counts",
        json=_draft_payload(counted_at="2025-01-01T09:00:00+00:00"),
    ).json()
    second = client.post(
        "/api/v1/inventory/counts",
        json=_draft_payload(counted_at="2025-01-02T09:00:00+00:00"),
    ).json()

    history = client.get("/api/v1/inventory/counts", params={"cafe_id": TEST_CAFE_ID})
    detail = client.get(
        f"/api/v1/inventory/counts/{first['count_id']}", params={"cafe_id": TEST_CAFE_ID}
    )
    catalog = client.get("/api/v1/inventory/items", params={"cafe_id": TEST_CAFE_ID})

    assert history.status_code == 200
    assert [count["count_id"] for count in history.json()] == [second["count_id"], first["count_id"]]
    assert history.json()[0]["item_count"] == 2
    assert detail.status_code == 200
    assert [line["ingredient_id"] for line in detail.json()["lines"]] == [
        "TEST_COUNT_BEANS",
        "TEST_COUNT_OAT",
    ]
    assert catalog.status_code == 200


@pytest.mark.database
def test_counts_are_not_visible_across_cafes_and_lines_have_no_edit_api(
    client: TestClient, count_catalog: None, database_session: Session
) -> None:
    created = client.post("/api/v1/inventory/counts", json=_draft_payload()).json()

    foreign_history = client.get(
        "/api/v1/inventory/counts", params={"cafe_id": OTHER_CAFE_ID}
    )
    foreign_detail = client.get(
        f"/api/v1/inventory/counts/{created['count_id']}", params={"cafe_id": OTHER_CAFE_ID}
    )
    foreign_verify = client.post(
        f"/api/v1/inventory/counts/{created['count_id']}/verify",
        params={"cafe_id": OTHER_CAFE_ID},
        json={"counted_at": "2025-01-03T09:00:00+00:00"},
    )
    verified = client.post(
        f"/api/v1/inventory/counts/{created['count_id']}/verify",
        params={"cafe_id": TEST_CAFE_ID},
        json={"counted_at": "2025-01-03T09:00:00+00:00"},
    )
    edit_attempt = client.patch(
        f"/api/v1/inventory/counts/{created['count_id']}", json={"status": "verified"}
    )
    void_count = StockCount(
        count_id="TEST_VOID_COUNT",
        cafe_id=TEST_CAFE_ID,
        counted_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        recorded_at=datetime.now(timezone.utc),
        status="void",
        is_synthetic=True,
    )
    database_session.add(void_count)
    database_session.flush()
    void_verify = client.post(
        "/api/v1/inventory/counts/TEST_VOID_COUNT/verify",
        params={"cafe_id": TEST_CAFE_ID},
        json={},
    )

    assert foreign_history.status_code == 200
    assert foreign_history.json() == []
    assert foreign_detail.status_code == 404
    assert foreign_verify.status_code == 404
    assert verified.status_code == 200
    assert edit_attempt.status_code == 405
    assert void_verify.status_code == 409
