import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine
from backend.models.cafe import Cafe
from backend.modules.sales.models import MenuProduct, Sale
from scripts.seed_sales import (
    SalesSeedCatalog,
    SalesSeedProduct,
    SeedConflictError,
    SeedValidationError,
    load_sales_catalog,
    seed_sales,
)

TEST_CAFE_ID = "TEST_SALES_SEED_CAFE"


def _catalog() -> SalesSeedCatalog:
    return SalesSeedCatalog(
        cafe_id=TEST_CAFE_ID,
        products=(
            SalesSeedProduct("TEST_SEED_LATTE", "Test Latte", "coffee", 500),
            SalesSeedProduct("TEST_SEED_MUFFIN", "Test Muffin", "bakery", 400),
        ),
    )


@pytest.fixture
def database_session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    session.add(Cafe(cafe_id=TEST_CAFE_ID, name="Sales Seed Test", timezone="UTC"))
    session.flush()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def test_source_catalog_contains_the_existing_eighteen_products() -> None:
    catalog = load_sales_catalog()

    assert catalog.cafe_id == "CAFE001"
    assert len(catalog.products) == 18
    assert {product.product_id for product in catalog.products} >= {"COF001", "BAK006"}
    assert {product.price_cents for product in catalog.products} <= {
        300,
        350,
        400,
        450,
        500,
        550,
    }


def test_source_catalog_validates_every_product_before_seeding(tmp_path) -> None:
    source_path = tmp_path / "menu.json"
    source_path.write_text(
        json.dumps(
            {
                "cafe_id": "TEST_CAFE",
                "products": [
                    {
                        "id": "VALID",
                        "name": "Valid",
                        "category": "test",
                        "price_cents": 100,
                    },
                    {
                        "id": "INVALID",
                        "name": "Invalid",
                        "category": "test",
                        "price_cents": 10.5,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SeedValidationError, match="supported nonnegative integer"):
        load_sales_catalog(source_path)


def test_source_catalog_rejects_duplicate_product_ids(tmp_path) -> None:
    source_path = tmp_path / "menu.json"
    source_path.write_text(
        json.dumps(
            {
                "cafe_id": "TEST_CAFE",
                "products": [
                    {"id": "DUP", "name": "One", "category": "test", "price_cents": 1},
                    {"id": "DUP", "name": "Two", "category": "test", "price_cents": 2},
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SeedValidationError, match="Duplicate product id"):
        load_sales_catalog(source_path)


@pytest.mark.database
def test_seed_creates_products_is_idempotent_and_never_creates_sales(
    database_session: Session,
) -> None:
    first_result = seed_sales(database_session, _catalog())
    second_result = seed_sales(database_session, _catalog())

    assert first_result.products_created == 2
    assert second_result.products_created == 0
    assert database_session.scalar(
        select(func.count())
        .select_from(MenuProduct)
        .where(MenuProduct.cafe_id == TEST_CAFE_ID)
    ) == 2
    assert database_session.scalar(
        select(func.count()).select_from(Sale).where(Sale.cafe_id == TEST_CAFE_ID)
    ) == 0


@pytest.mark.database
def test_seed_preserves_matches_and_rolls_back_all_products_on_conflict(
    database_session: Session,
) -> None:
    seed_sales(database_session, _catalog())
    conflicting_catalog = SalesSeedCatalog(
        cafe_id=TEST_CAFE_ID,
        products=(
            SalesSeedProduct("TEST_SEED_NEW", "New Product", "test", 100),
            SalesSeedProduct("TEST_SEED_LATTE", "Changed Latte", "coffee", 500),
        ),
    )

    with pytest.raises(SeedConflictError, match="Conflicting menu product"):
        seed_sales(database_session, conflicting_catalog)

    assert database_session.get(MenuProduct, "TEST_SEED_NEW") is None
    assert database_session.get(MenuProduct, "TEST_SEED_LATTE").name == "Test Latte"


@pytest.mark.database
def test_seed_requires_an_existing_cafe(database_session: Session) -> None:
    catalog = SalesSeedCatalog(
        cafe_id="TEST_SALES_MISSING_CAFE",
        products=(SalesSeedProduct("TEST_MISSING_CAFE_PRODUCT", "Test", "test", 1),),
    )

    with pytest.raises(SeedConflictError, match="Café does not exist"):
        seed_sales(database_session, catalog)
