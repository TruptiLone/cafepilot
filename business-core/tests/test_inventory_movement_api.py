from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier, Lock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import SessionLocal, engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.inventory.models import (
    InventoryItem,
    InventoryMovement,
    StockCount,
)
from backend.modules.inventory.repository import InventoryRepository
from backend.modules.inventory.schemas import InventoryMovementCreate
from backend.modules.inventory.service import InventoryService

TEST_CAFE_ID = "TEST_MOVEMENT_API_CAFE"
OTHER_CAFE_ID = "TEST_MOVEMENT_API_OTHER_CAFE"


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
def movement_catalog(database_session: Session) -> None:
    database_session.add_all(
        [
            Cafe(cafe_id=TEST_CAFE_ID, name="Movement API Test", timezone="UTC"),
            Cafe(cafe_id=OTHER_CAFE_ID, name="Other Movement API Test", timezone="UTC"),
            InventoryItem(
                ingredient_id="TEST_MOVEMENT_OAT",
                cafe_id=TEST_CAFE_ID,
                name="Movement Oat Milk",
                category="dairy",
                base_unit="mL",
            ),
            InventoryItem(
                ingredient_id="TEST_MOVEMENT_BEANS",
                cafe_id=TEST_CAFE_ID,
                name="Movement Beans",
                category="coffee",
                base_unit="g",
            ),
            InventoryItem(
                ingredient_id="TEST_MOVEMENT_OTHER",
                cafe_id=OTHER_CAFE_ID,
                name="Other Movement Item",
                category="coffee",
                base_unit="g",
            ),
        ]
    )
    database_session.flush()


def _movement_payload(**overrides) -> dict:
    payload = {
        "cafe_id": TEST_CAFE_ID,
        "ingredient_id": "TEST_MOVEMENT_OAT",
        "movement_type": "RECEIPT",
        "quantity_delta": "8000.000",
        "source_type": "supplier_delivery",
        "source_id": "TEST_DELIVERY_001",
        "occurred_at": "2025-01-02T09:00:00-08:00",
        "recorded_by": "EMP_TEST",
        "reason": "Accepted delivery",
    }
    payload.update(overrides)
    return payload


@pytest.mark.database
def test_receipt_creation_is_append_only_and_does_not_create_counts(
    client: TestClient, movement_catalog: None, database_session: Session
) -> None:
    response = client.post("/api/v1/inventory/movements", json=_movement_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["movement_type"] == "RECEIPT"
    assert body["quantity_delta"] == "8000.000"
    assert body["base_unit"] == "mL"
    assert body["is_estimated"] is False
    assert database_session.scalar(select(func.count()).select_from(StockCount)) == 0


@pytest.mark.database
@pytest.mark.parametrize(
    ("movement_type", "quantity_delta"),
    [("WASTE", "-12.500"), ("ADJUSTMENT", "3"), ("ADJUSTMENT", "-3")],
)
def test_waste_and_adjustments_create_signed_events(
    client: TestClient,
    movement_catalog: None,
    movement_type: str,
    quantity_delta: str,
) -> None:
    response = client.post(
        "/api/v1/inventory/movements",
        json=_movement_payload(
            movement_type=movement_type,
            quantity_delta=quantity_delta,
            source_id=f"TEST_{movement_type}_{quantity_delta}",
            reason="Recorded correction",
        ),
    )

    assert response.status_code == 201
    assert response.json()["movement_type"] == movement_type


@pytest.mark.database
@pytest.mark.parametrize(
    "payload",
    [
        _movement_payload(movement_type="RECEIPT", quantity_delta="-1"),
        _movement_payload(movement_type="WASTE", quantity_delta="1", reason="Waste"),
        _movement_payload(movement_type="WASTE", quantity_delta="-1", reason=""),
        _movement_payload(movement_type="ADJUSTMENT", quantity_delta="0", reason="Correction"),
        _movement_payload(quantity_delta="NaN"),
        _movement_payload(quantity_delta="Infinity"),
        _movement_payload(quantity_delta="1.0001"),
        _movement_payload(quantity_delta="1000000000000000"),
        _movement_payload(source_type=" "),
        _movement_payload(occurred_at="2025-01-02T09:00:00"),
        _movement_payload(occurred_at="2100-01-02T09:00:00+00:00"),
    ],
)
def test_invalid_movement_requests_leave_no_writes(
    client: TestClient, movement_catalog: None, database_session: Session, payload: dict
) -> None:
    before = database_session.scalar(select(func.count()).select_from(InventoryMovement))

    response = client.post("/api/v1/inventory/movements", json=payload)

    assert response.status_code == 422
    assert database_session.scalar(select(func.count()).select_from(InventoryMovement)) == before


@pytest.mark.database
def test_movement_identity_is_idempotent_and_conflicts_are_rejected(
    client: TestClient, movement_catalog: None, database_session: Session
) -> None:
    first = client.post("/api/v1/inventory/movements", json=_movement_payload())
    retry = client.post("/api/v1/inventory/movements", json=_movement_payload())
    conflict = client.post(
        "/api/v1/inventory/movements",
        json=_movement_payload(quantity_delta="7000.000"),
    )

    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json()["movement_id"] == first.json()["movement_id"]
    assert conflict.status_code == 409
    assert database_session.scalar(select(func.count()).select_from(InventoryMovement)) == 1


@pytest.mark.database
def test_concurrent_submissions_use_independent_sessions_without_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cafe_id = f"TEST_MOVEMENT_CONCURRENT_{uuid4().hex}"
    ingredient_id = f"TEST_MOVEMENT_ITEM_{uuid4().hex}"
    source_id = f"TEST_MOVEMENT_SOURCE_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(
            Cafe.__table__.insert(),
            {"cafe_id": cafe_id, "name": "Concurrent Movement Test", "timezone": "UTC"},
        )
        connection.execute(
            InventoryItem.__table__.insert(),
            {
                "ingredient_id": ingredient_id,
                "cafe_id": cafe_id,
                "name": "Concurrent Test Item",
                "category": "test",
                "base_unit": "g",
                "is_perishable": False,
                "active": True,
            },
        )

    request = InventoryMovementCreate(
        cafe_id=cafe_id,
        ingredient_id=ingredient_id,
        movement_type="RECEIPT",
        quantity_delta="1",
        source_type="concurrent_test",
        source_id=source_id,
        occurred_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    barrier = Barrier(2)
    lock = Lock()
    original_lookup = InventoryRepository.get_movement_by_identity
    lookup_count = 0

    def synchronize_initial_lookups(self, lookup_ingredient_id, source_type, lookup_source_id):
        nonlocal lookup_count
        if lookup_ingredient_id == ingredient_id and lookup_source_id == source_id:
            with lock:
                lookup_count += 1
                wait_for_peer = lookup_count <= 2
            if wait_for_peer:
                barrier.wait(timeout=5)
        return original_lookup(self, lookup_ingredient_id, source_type, lookup_source_id)

    def submit() -> tuple[str, bool]:
        with SessionLocal() as session:
            movement, created = InventoryService(session).create_movement(request)
            return movement.movement_id, created

    monkeypatch.setattr(
        InventoryRepository,
        "get_movement_by_identity",
        synchronize_initial_lookups,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: submit(), range(2)))

        assert {created for _, created in results} == {False, True}
        assert len({movement_id for movement_id, _ in results}) == 1
        with SessionLocal() as session:
            assert session.scalar(
                select(func.count()).select_from(InventoryMovement).where(
                    InventoryMovement.ingredient_id == ingredient_id
                )
            ) == 1
    finally:
        with engine.begin() as connection:
            connection.execute(
                delete(InventoryMovement).where(InventoryMovement.ingredient_id == ingredient_id)
            )
            connection.execute(
                delete(InventoryItem).where(InventoryItem.ingredient_id == ingredient_id)
            )
            connection.execute(delete(Cafe).where(Cafe.cafe_id == cafe_id))


@pytest.mark.database
def test_same_source_event_supports_multiple_ingredients_and_history_filters(
    client: TestClient, movement_catalog: None
) -> None:
    first = client.post("/api/v1/inventory/movements", json=_movement_payload()).json()
    second = client.post(
        "/api/v1/inventory/movements",
        json=_movement_payload(
            ingredient_id="TEST_MOVEMENT_BEANS",
            quantity_delta="1000",
            source_id="TEST_DELIVERY_001",
            occurred_at="2025-01-03T09:00:00+00:00",
        ),
    ).json()
    third = client.post(
        "/api/v1/inventory/movements",
        json=_movement_payload(
            movement_type="WASTE",
            quantity_delta="-10",
            source_id="TEST_WASTE_001",
            occurred_at="2025-01-04T09:00:00+00:00",
            reason="Spoiled product",
        ),
    ).json()

    history = client.get("/api/v1/inventory/movements", params={"cafe_id": TEST_CAFE_ID})
    filtered = client.get(
        "/api/v1/inventory/movements",
        params={"cafe_id": TEST_CAFE_ID, "ingredient_id": "TEST_MOVEMENT_OAT"},
    )
    waste_only = client.get(
        "/api/v1/inventory/movements",
        params={"cafe_id": TEST_CAFE_ID, "movement_type": "WASTE"},
    )

    assert [movement["movement_id"] for movement in history.json()] == [
        third["movement_id"],
        second["movement_id"],
        first["movement_id"],
    ]
    assert [movement["movement_id"] for movement in filtered.json()] == [
        third["movement_id"],
        first["movement_id"]
    ]
    assert [movement["movement_id"] for movement in waste_only.json()] == [
        third["movement_id"]
    ]


@pytest.mark.database
def test_cross_cafe_and_unknown_resources_are_rejected_without_writes(
    client: TestClient, movement_catalog: None, database_session: Session
) -> None:
    before = database_session.scalar(select(func.count()).select_from(InventoryMovement))
    unknown_cafe = client.post(
        "/api/v1/inventory/movements", json=_movement_payload(cafe_id="UNKNOWN_CAFE")
    )
    unknown_item = client.post(
        "/api/v1/inventory/movements",
        json=_movement_payload(ingredient_id="UNKNOWN_ITEM"),
    )
    cross_cafe = client.post(
        "/api/v1/inventory/movements",
        json=_movement_payload(ingredient_id="TEST_MOVEMENT_OTHER"),
    )
    foreign_history = client.get(
        "/api/v1/inventory/movements", params={"cafe_id": OTHER_CAFE_ID}
    )

    assert [response.status_code for response in (unknown_cafe, unknown_item, cross_cafe)] == [404, 404, 404]
    assert foreign_history.status_code == 200
    assert foreign_history.json() == []
    assert database_session.scalar(select(func.count()).select_from(InventoryMovement)) == before


@pytest.mark.database
def test_existing_catalog_and_count_routes_remain_available(
    client: TestClient, movement_catalog: None
) -> None:
    catalog = client.get("/api/v1/inventory/items", params={"cafe_id": TEST_CAFE_ID})
    counts = client.get("/api/v1/inventory/counts", params={"cafe_id": TEST_CAFE_ID})

    assert catalog.status_code == 200
    assert counts.status_code == 200
