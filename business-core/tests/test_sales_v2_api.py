from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier, Lock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import SessionLocal, engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.inventory.models import (
    InventoryMovement,
    StockCount,
    StockCountLine,
)
from backend.modules.sales.models import (
    MenuProduct,
    Modifier,
    ProductVariation,
    RecipeLine,
    Sale,
    SaleItem,
    SaleItemModifier,
)
from backend.modules.sales.repository import SalesRepository
from backend.modules.sales.schemas import SaleCreate
from backend.modules.sales.service import SalesService
from scripts.seed_sales_catalog import catalog_identifier
from tests.sales_v2_support import (
    delete_sales_v2_test_catalog,
    seed_sales_v2_test_catalog,
)

TEST_CAFE_ID = "TEST_SALES_V2_CAFE"
SOLD_AT = datetime(2026, 10, 27, 16, 15, tzinfo=timezone.utc)


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
def sales_v2_catalog(database_session: Session) -> None:
    seed_sales_v2_test_catalog(database_session, TEST_CAFE_ID)


def _id(local_id: str, cafe_id: str = TEST_CAFE_ID) -> str:
    return catalog_identifier(cafe_id, local_id)


def _worked_payload(**overrides) -> dict:
    payload = {
        "cafe_id": TEST_CAFE_ID,
        "source_id": "TEST_V2_POS_001",
        "source": "pos",
        "sold_at": SOLD_AT.isoformat(),
        "discount_cents": 100,
        "items": [
            {
                "product_id": _id("COF003"),
                "variation_id": _id("COF003_LRG"),
                "quantity": 2,
                "modifiers": [
                    {"modifier_id": _id("MILK_OAT"), "quantity": 1},
                    {"modifier_id": _id("EXTRA_SHOT"), "quantity": 1},
                ],
            }
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.database
def test_v2_pricing_usage_movements_and_stock_cutoff_match_worked_example(
    client: TestClient,
    sales_v2_catalog: None,
    database_session: Session,
) -> None:
    counted_at = SOLD_AT - timedelta(days=1)
    database_session.add(
        StockCount(
            count_id="TEST_V2_BEANS_COUNT",
            cafe_id=TEST_CAFE_ID,
            counted_at=counted_at,
            recorded_at=counted_at,
            status="verified",
            is_synthetic=True,
        )
    )
    database_session.add(
        StockCountLine(
            count_line_id="TEST_V2_BEANS_COUNT_LINE",
            count_id="TEST_V2_BEANS_COUNT",
            cafe_id=TEST_CAFE_ID,
            ingredient_id=_id("COFFEE_BEANS"),
            counted_quantity=Decimal("100.000"),
        )
    )
    database_session.flush()

    response = client.post("/api/v1/sales", json=_worked_payload())

    assert response.status_code == 201
    body = response.json()
    assert (
        body["subtotal_cents"],
        body["discount_cents"],
        body["tax_cents"],
        body["total_cents"],
    ) == (1500, 100, 123, 1523)
    assert body["items"][0]["unit_price_cents"] == 575
    assert body["items"][0]["modifier_unit_cents"] == 175
    assert body["items"][0]["line_total_cents"] == 1500
    assert {modifier["modifier_id"] for modifier in body["items"][0]["modifiers"]} == {
        _id("MILK_OAT"),
        _id("EXTRA_SHOT"),
    }

    movements = list(
        database_session.scalars(
            select(InventoryMovement)
            .where(
                InventoryMovement.cafe_id == TEST_CAFE_ID,
                InventoryMovement.source_type == "sale_item",
            )
            .order_by(InventoryMovement.ingredient_id)
        )
    )
    assert {
        movement.ingredient_id: (
            movement.quantity_delta,
            movement.is_estimated,
            movement.movement_type,
        )
        for movement in movements
    } == {
        _id("COFFEE_BEANS"): (Decimal("-54.000"), True, "SALE_USAGE"),
        _id("OAT_MILK"): (Decimal("-640.000"), True, "SALE_USAGE"),
    }
    assert len({movement.source_id for movement in movements}) == 1

    usage = client.get(
        f"/api/v1/sales/{body['sale_id']}/ingredient-usage",
        params={"cafe_id": TEST_CAFE_ID},
    )
    assert usage.status_code == 200
    assert usage.json()["usage_status"] == "computed"
    assert usage.json()["movement_posted"] is True
    assert usage.json()["is_estimated"] is True
    assert {
        item["ingredient_id"]: (item["quantity"], item["unit"])
        for item in usage.json()["ingredients"]
    } == {
        _id("COFFEE_BEANS"): ("54.000", "g"),
        _id("OAT_MILK"): ("640.000", "mL"),
    }
    assert _id("WHOLE_MILK") not in {
        item["ingredient_id"] for item in usage.json()["ingredients"]
    }

    beans_stock = client.get(
        f"/api/v1/inventory/stock/{_id('COFFEE_BEANS')}",
        params={"cafe_id": TEST_CAFE_ID},
    ).json()
    oat_stock = client.get(
        f"/api/v1/inventory/stock/{_id('OAT_MILK')}",
        params={"cafe_id": TEST_CAFE_ID},
    ).json()
    assert beans_stock["estimated_quantity"] == "46.000"
    assert beans_stock["has_estimated_movements"] is True
    assert oat_stock["stock_status"] == "unknown"


@pytest.mark.database
def test_v2_retry_is_idempotent_and_conflicts_do_not_duplicate_usage(
    client: TestClient,
    sales_v2_catalog: None,
    database_session: Session,
) -> None:
    first = client.post("/api/v1/sales", json=_worked_payload())
    database_session.get(ProductVariation, _id("COF003_LRG")).price_cents = 999
    database_session.get(Modifier, _id("MILK_OAT")).price_cents = 222
    database_session.get(ProductVariation, _id("COF003_LRG")).active = False
    database_session.get(Modifier, _id("MILK_OAT")).active = False
    for recipe in database_session.scalars(
        select(RecipeLine).where(
            RecipeLine.cafe_id == TEST_CAFE_ID,
            RecipeLine.variation_id == _id("COF003_LRG"),
        )
    ):
        database_session.delete(recipe)
    database_session.flush()
    retry = client.post("/api/v1/sales", json=_worked_payload())
    conflicts = [
        client.post(
            "/api/v1/sales",
            json=_worked_payload(discount_cents=99),
        ),
        client.post(
            "/api/v1/sales",
            json=_worked_payload(
                sold_at=(SOLD_AT - timedelta(minutes=1)).isoformat()
            ),
        ),
        client.post(
            "/api/v1/sales",
            json=_worked_payload(
                items=[
                    {
                        "variation_id": _id("COF003_LRG"),
                        "quantity": 2,
                        "modifiers": [
                            {
                                "modifier_id": _id("MILK_ALMOND"),
                                "quantity": 1,
                            },
                            {
                                "modifier_id": _id("EXTRA_SHOT"),
                                "quantity": 1,
                            },
                        ],
                    }
                ]
            ),
        ),
        client.post(
            "/api/v1/sales",
            json=_worked_payload(
                source="generated",
                external_id="TEST_CONFLICTING_SOURCE",
            ),
        ),
    ]

    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json()["sale_id"] == first.json()["sale_id"]
    assert retry.json()["total_cents"] == 1523
    assert all(conflict.status_code == 409 for conflict in conflicts)
    assert database_session.scalar(
        select(func.count()).select_from(Sale).where(
            Sale.cafe_id == TEST_CAFE_ID,
            Sale.source_id == "TEST_V2_POS_001",
        )
    ) == 1
    assert database_session.scalar(
        select(func.count()).select_from(InventoryMovement).where(
            InventoryMovement.cafe_id == TEST_CAFE_ID,
            InventoryMovement.source_type == "sale_item",
        )
    ) == 2


@pytest.mark.database
def test_syrup_modifier_effects_add_exact_quantities(
    client: TestClient,
    sales_v2_catalog: None,
) -> None:
    response = client.post(
        "/api/v1/sales",
        json=_worked_payload(
            source_id="TEST_V2_SYRUPS",
            discount_cents=0,
            items=[
                {
                    "variation_id": _id("COF003_REG"),
                    "quantity": 2,
                    "modifiers": [
                        {"modifier_id": _id("EXTRA_VANILLA"), "quantity": 1},
                        {"modifier_id": _id("EXTRA_CARAMEL"), "quantity": 1},
                    ],
                }
            ],
        ),
    )

    assert response.status_code == 201
    usage = client.get(
        f"/api/v1/sales/{response.json()['sale_id']}/ingredient-usage",
        params={"cafe_id": TEST_CAFE_ID},
    ).json()
    quantities = {
        item["ingredient_id"]: item["quantity"]
        for item in usage["ingredients"]
    }
    assert quantities[_id("VANILLA_SYRUP")] == "30.000"
    assert quantities[_id("CARAMEL_SYRUP")] == "30.000"


@pytest.mark.database
def test_multiple_configurations_of_same_product_are_supported(
    client: TestClient,
    sales_v2_catalog: None,
    database_session: Session,
) -> None:
    payload = _worked_payload(
        source_id="TEST_V2_MULTI_LINE",
        discount_cents=0,
        items=[
            {
                "variation_id": _id("COF003_REG"),
                "quantity": 1,
                "modifiers": [],
            },
            {
                "variation_id": _id("COF003_LRG"),
                "quantity": 1,
                "modifiers": [
                    {"modifier_id": _id("MILK_OAT"), "quantity": 1}
                ],
            },
        ],
    )

    response = client.post("/api/v1/sales", json=payload)

    assert response.status_code == 201
    assert len(response.json()["items"]) == 2
    assert database_session.scalar(
        select(func.count()).select_from(SaleItem).where(
            SaleItem.sale_id == response.json()["sale_id"]
        )
    ) == 2
    modifiers = list(
        database_session.scalars(
            select(SaleItemModifier).where(
                SaleItemModifier.cafe_id == TEST_CAFE_ID
            )
        )
    )
    assert {modifier.modifier_id for modifier in modifiers} == {
        _id("MILK_WHOLE"),
        _id("MILK_OAT"),
    }


@pytest.mark.database
@pytest.mark.parametrize(
    "item",
    [
        {
            "variation_id": catalog_identifier(TEST_CAFE_ID, "COF003_LRG"),
            "quantity": 1,
            "modifiers": [
                {
                    "modifier_id": catalog_identifier(TEST_CAFE_ID, "MILK_OAT"),
                    "quantity": 1,
                },
                {
                    "modifier_id": catalog_identifier(TEST_CAFE_ID, "MILK_OAT"),
                    "quantity": 1,
                },
            ],
        },
        {
            "variation_id": catalog_identifier(TEST_CAFE_ID, "COF003_LRG"),
            "quantity": 1,
            "modifiers": [
                {
                    "modifier_id": catalog_identifier(TEST_CAFE_ID, "MILK_OAT"),
                    "quantity": 1,
                },
                {
                    "modifier_id": catalog_identifier(TEST_CAFE_ID, "MILK_ALMOND"),
                    "quantity": 1,
                },
            ],
        },
        {
            "variation_id": catalog_identifier(TEST_CAFE_ID, "COF003_LRG"),
            "quantity": 1,
            "modifiers": [
                {
                    "modifier_id": catalog_identifier(TEST_CAFE_ID, "EXTRA_SHOT"),
                    "quantity": 2,
                }
            ],
        },
        {
            "variation_id": catalog_identifier(TEST_CAFE_ID, "BAK001_REG"),
            "quantity": 1,
            "modifiers": [
                {
                    "modifier_id": catalog_identifier(TEST_CAFE_ID, "EXTRA_SHOT"),
                    "quantity": 1,
                }
            ],
        },
    ],
)
def test_invalid_modifier_combinations_are_rejected_without_partial_sales(
    client: TestClient,
    sales_v2_catalog: None,
    database_session: Session,
    item: dict,
) -> None:
    response = client.post(
        "/api/v1/sales",
        json=_worked_payload(
            source_id=f"TEST_INVALID_{uuid4().hex}",
            discount_cents=0,
            items=[item],
        ),
    )

    assert response.status_code == 422
    assert database_session.scalar(
        select(func.count()).select_from(Sale).where(Sale.cafe_id == TEST_CAFE_ID)
    ) == 0


@pytest.mark.database
def test_inactive_catalog_and_variation_product_mismatch_are_rejected(
    client: TestClient,
    sales_v2_catalog: None,
    database_session: Session,
) -> None:
    variation = database_session.get(ProductVariation, _id("COF003_LRG"))
    variation.active = False
    database_session.flush()
    inactive_variation = client.post(
        "/api/v1/sales",
        json=_worked_payload(source_id="TEST_INACTIVE_VARIATION"),
    )
    variation.active = True
    modifier = database_session.get(Modifier, _id("MILK_OAT"))
    modifier.active = False
    database_session.flush()
    inactive_modifier = client.post(
        "/api/v1/sales",
        json=_worked_payload(source_id="TEST_INACTIVE_MODIFIER"),
    )
    modifier.active = True
    database_session.flush()
    mismatch = client.post(
        "/api/v1/sales",
        json=_worked_payload(
            source_id="TEST_VARIATION_MISMATCH",
            items=[
                {
                    "product_id": _id("COF004"),
                    "variation_id": _id("COF003_LRG"),
                    "quantity": 1,
                    "modifiers": [
                        {"modifier_id": _id("MILK_OAT"), "quantity": 1}
                    ],
                }
            ],
        ),
    )

    assert inactive_variation.status_code == 404
    assert inactive_modifier.status_code == 422
    assert mismatch.status_code == 422
    assert database_session.scalar(
        select(func.count()).select_from(Sale).where(Sale.cafe_id == TEST_CAFE_ID)
    ) == 0


@pytest.mark.database
@pytest.mark.parametrize("source", ["generated", "csv_import"])
def test_historical_sources_compute_usage_without_posting_movements(
    client: TestClient,
    sales_v2_catalog: None,
    database_session: Session,
    source: str,
) -> None:
    payload = _worked_payload(
        source_id=f"TEST_{source}_SOURCE",
        source=source,
        external_id=f"TEST_{source}_EXTERNAL",
    )
    response = client.post("/api/v1/sales", json=payload)

    assert response.status_code == 201
    assert database_session.scalar(
        select(func.count()).select_from(InventoryMovement).where(
            InventoryMovement.cafe_id == TEST_CAFE_ID
        )
    ) == 0
    usage = client.get(
        f"/api/v1/sales/{response.json()['sale_id']}/ingredient-usage",
        params={"cafe_id": TEST_CAFE_ID},
    ).json()
    assert usage["usage_status"] == "computed"
    assert usage["movement_posted"] is False
    assert usage["is_estimated"] is True


@pytest.mark.database
def test_v1_stays_zero_tax_and_unmapped_legacy_sales_are_explicit(
    client: TestClient,
    sales_v2_catalog: None,
    database_session: Session,
) -> None:
    missing_product_id = _id("MISSING_RECIPE_PRODUCT")
    missing_variation_id = _id("MISSING_RECIPE_PRODUCT_REG")
    database_session.add(
        MenuProduct(
            product_id=missing_product_id,
            cafe_id=TEST_CAFE_ID,
            name="Missing Recipe",
            category="test",
            price_cents=250,
        )
    )
    database_session.flush()
    database_session.add(
        ProductVariation(
            variation_id=missing_variation_id,
            cafe_id=TEST_CAFE_ID,
            product_id=missing_product_id,
            code="REG",
            name="Regular",
            price_cents=250,
        )
    )
    database_session.flush()

    v2 = client.post(
        "/api/v1/sales",
        json=_worked_payload(
            source_id="TEST_V2_MISSING_RECIPE",
            discount_cents=0,
            items=[
                {
                    "variation_id": missing_variation_id,
                    "quantity": 1,
                    "modifiers": [],
                }
            ],
        ),
    )
    v1_payload = {
        "cafe_id": TEST_CAFE_ID,
        "source_id": "TEST_V1_MISSING_RECIPE",
        "sold_at": SOLD_AT.isoformat(),
        "items": [{"product_id": missing_product_id, "quantity": 1}],
    }
    v1 = client.post("/api/v1/sales", json=v1_payload)
    retry = client.post("/api/v1/sales", json=v1_payload)

    assert v2.status_code == 422
    assert v1.status_code == 201
    assert retry.status_code == 200
    assert v1.json()["discount_cents"] == 0
    assert v1.json()["tax_cents"] == 0
    assert v1.json()["total_cents"] == 250
    usage = client.get(
        f"/api/v1/sales/{v1.json()['sale_id']}/ingredient-usage",
        params={"cafe_id": TEST_CAFE_ID},
    ).json()
    assert usage["usage_status"] == "unmapped"
    assert usage["ingredients"] == []
    assert usage["missing_recipe_variations"] == [missing_variation_id]
    assert database_session.scalar(
        select(func.count()).select_from(InventoryMovement).where(
            InventoryMovement.cafe_id == TEST_CAFE_ID
        )
    ) == 0


@pytest.mark.database
def test_mapped_v1_sale_posts_usage_once_with_zero_tax(
    client: TestClient,
    sales_v2_catalog: None,
    database_session: Session,
) -> None:
    payload = {
        "cafe_id": TEST_CAFE_ID,
        "source_id": "TEST_V1_MAPPED_RECIPE",
        "sold_at": SOLD_AT.isoformat(),
        "items": [{"product_id": _id("COF003"), "quantity": 1}],
    }
    first = client.post("/api/v1/sales", json=payload)
    retry = client.post("/api/v1/sales", json=payload)

    assert first.status_code == 201
    assert retry.status_code == 200
    assert first.json()["subtotal_cents"] == 500
    assert first.json()["discount_cents"] == 0
    assert first.json()["tax_cents"] == 0
    assert first.json()["total_cents"] == 500
    assert database_session.scalar(
        select(func.count()).select_from(InventoryMovement).where(
            InventoryMovement.cafe_id == TEST_CAFE_ID,
            InventoryMovement.source_type == "sale_item",
        )
    ) == 2


@pytest.mark.database
def test_inventory_posting_failure_rolls_back_sale_and_snapshots(
    client: TestClient,
    sales_v2_catalog: None,
    database_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_create = SalesRepository.create_sale

    def create_with_invalid_movement(
        repository,
        sale,
        items,
        modifiers,
        movements,
    ):
        movements[0].quantity_delta = abs(movements[0].quantity_delta)
        return original_create(repository, sale, items, modifiers, movements)

    monkeypatch.setattr(SalesRepository, "create_sale", create_with_invalid_movement)
    with pytest.raises(IntegrityError):
        client.post(
            "/api/v1/sales",
            json=_worked_payload(source_id="TEST_ATOMIC_FAILURE"),
        )

    assert database_session.scalar(
        select(func.count()).select_from(Sale).where(
            Sale.cafe_id == TEST_CAFE_ID,
            Sale.source_id == "TEST_ATOMIC_FAILURE",
        )
    ) == 0
    assert database_session.scalar(
        select(func.count()).select_from(SaleItem).where(
            SaleItem.cafe_id == TEST_CAFE_ID
        )
    ) == 0
    assert database_session.scalar(
        select(func.count()).select_from(SaleItemModifier).where(
            SaleItemModifier.cafe_id == TEST_CAFE_ID
        )
    ) == 0
    assert database_session.scalar(
        select(func.count()).select_from(InventoryMovement).where(
            InventoryMovement.cafe_id == TEST_CAFE_ID
        )
    ) == 0


@pytest.mark.database
def test_ingredient_usage_is_cafe_scoped(
    client: TestClient,
    sales_v2_catalog: None,
    database_session: Session,
) -> None:
    sale = client.post("/api/v1/sales", json=_worked_payload()).json()
    other_cafe_id = "TEST_SALES_V2_OTHER_CAFE"
    database_session.add(Cafe(cafe_id=other_cafe_id, name="Other", timezone="UTC"))
    database_session.flush()

    response = client.get(
        f"/api/v1/sales/{sale['sale_id']}/ingredient-usage",
        params={"cafe_id": other_cafe_id},
    )

    assert response.status_code == 404


@pytest.mark.database
def test_concurrent_v2_retries_use_one_sale_and_one_usage_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cafe_id = f"TEST_SALES_V2_CONCURRENT_{uuid4().hex}"
    source_id = f"TEST_SALES_V2_SOURCE_{uuid4().hex}"
    with SessionLocal.begin() as session:
        seed_sales_v2_test_catalog(session, cafe_id, tax_rate_bps=0)

    request = SaleCreate.model_validate(
        {
            **_worked_payload(),
            "cafe_id": cafe_id,
            "source_id": source_id,
            "discount_cents": 0,
            "items": [
                {
                    "variation_id": _id("COF003_LRG", cafe_id),
                    "quantity": 1,
                    "modifiers": [
                        {
                            "modifier_id": _id("MILK_OAT", cafe_id),
                            "quantity": 1,
                        }
                    ],
                }
            ],
        }
    )
    barrier = Barrier(2)
    lock = Lock()
    original_lookup = SalesRepository.get_sale_by_source
    lookup_count = 0

    def synchronized_lookup(repository, lookup_cafe_id, lookup_source_id):
        nonlocal lookup_count
        if lookup_cafe_id == cafe_id and lookup_source_id == source_id:
            with lock:
                lookup_count += 1
                wait_for_peer = lookup_count <= 2
            if wait_for_peer:
                barrier.wait(timeout=5)
        return original_lookup(repository, lookup_cafe_id, lookup_source_id)

    def submit() -> tuple[str, bool]:
        with SessionLocal() as session:
            sale, created = SalesService(session).create_sale(request)
            return sale.sale_id, created

    monkeypatch.setattr(
        SalesRepository,
        "get_sale_by_source",
        synchronized_lookup,
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: submit(), range(2)))

        assert {created for _, created in results} == {False, True}
        assert len({sale_id for sale_id, _ in results}) == 1
        with SessionLocal() as session:
            assert session.scalar(
                select(func.count()).select_from(Sale).where(Sale.cafe_id == cafe_id)
            ) == 1
            assert session.scalar(
                select(func.count()).select_from(InventoryMovement).where(
                    InventoryMovement.cafe_id == cafe_id
                )
            ) == 2
    finally:
        with SessionLocal.begin() as session:
            delete_sales_v2_test_catalog(session, cafe_id)
