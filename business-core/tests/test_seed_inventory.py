import json

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine
from backend.models.cafe import Cafe
from backend.modules.inventory.models import InventoryItem
from scripts.seed_inventory import (
    InventorySeedCatalog,
    InventorySeedItem,
    SeedConflictError,
    SeedValidationError,
    load_inventory_catalog,
    seed_inventory,
)


def _catalog(cafe_id: str = "TEST_SEED_CAFE") -> InventorySeedCatalog:
    return InventorySeedCatalog(
        cafe_id=cafe_id,
        items=(
            InventorySeedItem("TEST_SEED_COFFEE", "Test Coffee", "coffee", "g"),
            InventorySeedItem("TEST_SEED_MILK", "Test Milk", "dairy", "mL"),
        ),
    )


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


def test_source_catalog_contains_expected_synthetic_inventory() -> None:
    catalog = load_inventory_catalog()

    assert catalog.cafe_id == "CAFE001"
    assert len(catalog.items) == 19
    assert {item.ingredient_id for item in catalog.items} >= {
        "COFFEE_BEANS",
        "OAT_MILK",
    }
    assert {item.base_unit for item in catalog.items} == {"g", "mL", "each"}


def test_source_catalog_rejects_duplicate_ingredient_ids(tmp_path) -> None:
    source_path = tmp_path / "inventory.json"
    source_path.write_text(
        json.dumps(
            {
                "cafe_id": "TEST_CAFE",
                "inventory": [
                    {"ingredient_id": "DUPLICATE", "name": "One", "category": "test", "unit": "g"},
                    {"ingredient_id": "DUPLICATE", "name": "Two", "category": "test", "unit": "g"},
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SeedValidationError, match="Duplicate ingredient_id"):
        load_inventory_catalog(source_path)


def test_source_catalog_rejects_invalid_units(tmp_path) -> None:
    source_path = tmp_path / "inventory.json"
    source_path.write_text(
        json.dumps(
            {
                "cafe_id": "TEST_CAFE",
                "inventory": [
                    {"ingredient_id": "INVALID_UNIT", "name": "Item", "category": "test", "unit": "kg"}
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SeedValidationError, match="Unsupported unit"):
        load_inventory_catalog(source_path)


@pytest.mark.database
def test_seed_creates_catalog_and_is_idempotent(database_session: Session) -> None:
    catalog = _catalog()

    first_result = seed_inventory(database_session, catalog)
    second_result = seed_inventory(database_session, catalog)

    assert first_result.cafes_created == 1
    assert first_result.items_created == 2
    assert second_result.cafes_created == 0
    assert second_result.items_created == 0
    assert database_session.get(Cafe, catalog.cafe_id) is not None
    assert (
        database_session.execute(
            text(
                "SELECT count(*) FROM inventory_items "
                "WHERE cafe_id = :cafe_id"
            ),
            {"cafe_id": catalog.cafe_id},
        ).scalar_one()
        == 2
    )


@pytest.mark.database
def test_seed_preserves_matching_records_and_rejects_conflicts(
    database_session: Session,
) -> None:
    catalog = _catalog()
    seed_inventory(database_session, catalog)

    original_item = database_session.get(InventoryItem, "TEST_SEED_COFFEE")
    assert original_item is not None
    original_name = original_item.name

    assert seed_inventory(database_session, catalog).items_created == 0
    assert database_session.get(InventoryItem, "TEST_SEED_COFFEE").name == original_name

    conflicting_catalog = InventorySeedCatalog(
        cafe_id=catalog.cafe_id,
        items=(InventorySeedItem("TEST_SEED_COFFEE", "Changed", "coffee", "g"),),
    )
    with pytest.raises(SeedConflictError, match="Conflicting inventory item"):
        seed_inventory(database_session, conflicting_catalog)

    assert database_session.get(InventoryItem, "TEST_SEED_COFFEE").name == original_name


@pytest.mark.database
def test_seed_rolls_back_when_import_encounters_a_conflict(
    database_session: Session,
) -> None:
    database_session.add(Cafe(cafe_id="OTHER_CAFE", name="Other", timezone="UTC"))
    database_session.add(
        InventoryItem(
            ingredient_id="TEST_SEED_CONFLICT",
            cafe_id="OTHER_CAFE",
            name="Existing",
            category="test",
            base_unit="g",
        )
    )
    database_session.commit()

    catalog = InventorySeedCatalog(
        cafe_id="TEST_ROLLBACK_CAFE",
        items=(
            InventorySeedItem("TEST_ROLLBACK_NEW", "New", "test", "g"),
            InventorySeedItem("TEST_SEED_CONFLICT", "Conflict", "test", "g"),
        ),
    )

    with pytest.raises(SeedConflictError, match="Conflicting inventory item"):
        seed_inventory(database_session, catalog)

    assert database_session.get(Cafe, "TEST_ROLLBACK_CAFE") is None
    assert database_session.get(InventoryItem, "TEST_ROLLBACK_NEW") is None
