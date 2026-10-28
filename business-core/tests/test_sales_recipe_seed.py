from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine
from backend.models.cafe import Cafe
from backend.modules.inventory.models import InventoryItem
from backend.modules.sales.models import ModifierRecipeEffect, RecipeLine
from backend.modules.sales.repository import SalesRepository
from scripts.seed_sales_catalog import catalog_identifier
from scripts.seed_sales_recipes import (
    SalesRecipeSeedError,
    SalesRecipeSeedResult,
    seed_sales_recipes,
)
from tests.sales_v2_support import seed_sales_v2_test_catalog

TEST_CAFE_ID = "TEST_RECIPE_SEED_CAFE"


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
def recipe_prerequisites(database_session: Session) -> None:
    seed_sales_v2_test_catalog(
        database_session,
        TEST_CAFE_ID,
        include_recipes=False,
    )


@pytest.mark.database
def test_recipe_seed_maps_all_variations_and_is_idempotent(
    database_session: Session,
    recipe_prerequisites: None,
) -> None:
    first = seed_sales_recipes(database_session, TEST_CAFE_ID)
    second = seed_sales_recipes(database_session, TEST_CAFE_ID)

    assert first == SalesRecipeSeedResult(52, 6)
    assert second == SalesRecipeSeedResult(0, 0)
    assert database_session.scalar(
        select(func.count(func.distinct(RecipeLine.variation_id))).where(
            RecipeLine.cafe_id == TEST_CAFE_ID
        )
    ) == 30
    assert database_session.scalar(
        select(func.count()).select_from(RecipeLine).where(
            RecipeLine.cafe_id == TEST_CAFE_ID
        )
    ) == 52
    assert database_session.scalar(
        select(func.count()).select_from(ModifierRecipeEffect).where(
            ModifierRecipeEffect.cafe_id == TEST_CAFE_ID
        )
    ) == 6


@pytest.mark.database
def test_recipe_seed_rejects_conflicts_and_unit_mismatches(
    database_session: Session,
    recipe_prerequisites: None,
) -> None:
    seed_sales_recipes(database_session, TEST_CAFE_ID)
    recipe = database_session.scalar(
        select(RecipeLine).where(RecipeLine.cafe_id == TEST_CAFE_ID)
    )
    recipe.quantity = Decimal("999.000")
    database_session.flush()
    with pytest.raises(SalesRecipeSeedError, match="Conflicting existing recipe line"):
        seed_sales_recipes(database_session, TEST_CAFE_ID)


@pytest.mark.database
def test_recipe_seed_validates_inventory_units(
    database_session: Session,
    recipe_prerequisites: None,
) -> None:
    beans = database_session.get(
        InventoryItem,
        catalog_identifier(TEST_CAFE_ID, "COFFEE_BEANS"),
    )
    beans.base_unit = "mL"
    database_session.flush()
    with pytest.raises(SalesRecipeSeedError, match="Recipe unit conflicts"):
        seed_sales_recipes(database_session, TEST_CAFE_ID)


@pytest.mark.database
def test_recipe_version_selection_uses_latest_complete_effective_date(
    database_session: Session,
    recipe_prerequisites: None,
) -> None:
    seed_sales_recipes(database_session, TEST_CAFE_ID)
    variation_id = catalog_identifier(TEST_CAFE_ID, "COF003_LRG")
    effective_from = datetime(2026, 1, 1, tzinfo=timezone.utc)
    database_session.add_all(
        [
            RecipeLine(
                recipe_line_id="TEST_RECIPE_VERSION_BEANS",
                cafe_id=TEST_CAFE_ID,
                variation_id=variation_id,
                ingredient_id=catalog_identifier(TEST_CAFE_ID, "COFFEE_BEANS"),
                quantity=Decimal("20.000"),
                unit="g",
                effective_from=effective_from,
            ),
            RecipeLine(
                recipe_line_id="TEST_RECIPE_VERSION_MILK",
                cafe_id=TEST_CAFE_ID,
                variation_id=variation_id,
                ingredient_id=catalog_identifier(TEST_CAFE_ID, "WHOLE_MILK"),
                quantity=Decimal("300.000"),
                unit="mL",
                effective_from=effective_from,
            ),
        ]
    )
    database_session.flush()

    repository = SalesRepository(database_session)
    older = repository.get_recipe_lines(
        TEST_CAFE_ID,
        variation_id,
        datetime(2025, 6, 1, tzinfo=timezone.utc),
    )
    newer = repository.get_recipe_lines(
        TEST_CAFE_ID,
        variation_id,
        datetime(2026, 6, 1, tzinfo=timezone.utc),
    )

    assert {line.ingredient_id: line.quantity for line, _ in older} == {
        catalog_identifier(TEST_CAFE_ID, "COFFEE_BEANS"): Decimal("18.000"),
        catalog_identifier(TEST_CAFE_ID, "WHOLE_MILK"): Decimal("320.000"),
    }
    assert {line.ingredient_id: line.quantity for line, _ in newer} == {
        catalog_identifier(TEST_CAFE_ID, "COFFEE_BEANS"): Decimal("20.000"),
        catalog_identifier(TEST_CAFE_ID, "WHOLE_MILK"): Decimal("300.000"),
    }


@pytest.mark.database
def test_recipe_foreign_keys_reject_cross_cafe_ingredients(
    database_session: Session,
    recipe_prerequisites: None,
) -> None:
    other_cafe_id = "TEST_RECIPE_OTHER_CAFE"
    database_session.add(Cafe(cafe_id=other_cafe_id, name="Other", timezone="UTC"))
    database_session.add(
        InventoryItem(
            ingredient_id="TEST_RECIPE_OTHER_BEANS",
            cafe_id=other_cafe_id,
            name="Other Beans",
            category="coffee",
            base_unit="g",
        )
    )
    database_session.flush()
    with pytest.raises(IntegrityError):
        with database_session.begin_nested():
            database_session.add(
                RecipeLine(
                    recipe_line_id="TEST_RECIPE_CROSS_CAFE",
                    cafe_id=TEST_CAFE_ID,
                    variation_id=catalog_identifier(TEST_CAFE_ID, "COF003_LRG"),
                    ingredient_id="TEST_RECIPE_OTHER_BEANS",
                    quantity=Decimal("1.000"),
                    unit="g",
                    effective_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
                )
            )
            database_session.flush()
