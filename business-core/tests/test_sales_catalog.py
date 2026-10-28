import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine
from backend.models.cafe import Cafe
from backend.modules.sales.models import (
    MenuProduct,
    Modifier,
    ModifierGroup,
    ProductModifierGroup,
    ProductVariation,
)
from scripts.seed_sales import SalesSeedCatalog, load_sales_catalog, seed_sales
from scripts.seed_sales_catalog import (
    BAKERY_PRODUCT_IDS,
    EXTRAS_PRODUCT_IDS,
    MILK_PRODUCT_IDS,
    SalesCatalogSeedError,
    SalesCatalogSeedResult,
    build_sales_catalog_plan,
    catalog_for_cafe,
    catalog_identifier,
    seed_sales_catalog,
)

TEST_CAFE_ID = "TEST_SALES_CATALOG_CAFE"
OTHER_CAFE_ID = "TEST_SALES_CATALOG_OTHER_CAFE"


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
def sales_catalog(database_session: Session) -> SalesSeedCatalog:
    database_session.add(
        Cafe(cafe_id=TEST_CAFE_ID, name="Sales Catalog Test", timezone="UTC")
    )
    database_session.flush()
    catalog = catalog_for_cafe(load_sales_catalog(), TEST_CAFE_ID)
    seed_sales(database_session, catalog)
    return catalog


def _expect_integrity_error(database_session: Session, record: object) -> None:
    with pytest.raises(IntegrityError):
        with database_session.begin_nested():
            database_session.add(record)
            database_session.flush()


def _seeded_rows(database_session: Session, sales_catalog: SalesSeedCatalog):
    result = seed_sales_catalog(database_session, sales_catalog)
    variations = list(
        database_session.scalars(
            select(ProductVariation)
            .where(ProductVariation.cafe_id == TEST_CAFE_ID)
            .order_by(ProductVariation.variation_id)
        )
    )
    groups = list(
        database_session.scalars(
            select(ModifierGroup)
            .where(ModifierGroup.cafe_id == TEST_CAFE_ID)
            .order_by(ModifierGroup.group_id)
        )
    )
    modifiers = list(
        database_session.scalars(
            select(Modifier)
            .where(Modifier.cafe_id == TEST_CAFE_ID)
            .order_by(Modifier.modifier_id)
        )
    )
    mappings = list(
        database_session.scalars(
            select(ProductModifierGroup)
            .where(ProductModifierGroup.cafe_id == TEST_CAFE_ID)
            .order_by(ProductModifierGroup.product_modifier_group_id)
        )
    )
    return result, variations, groups, modifiers, mappings


@pytest.mark.database
def test_seed_preserves_products_and_creates_expected_variations(
    database_session: Session,
    sales_catalog: SalesSeedCatalog,
) -> None:
    products_before = {
        product.product_id: (product.name, product.category, product.price_cents)
        for product in database_session.scalars(
            select(MenuProduct).where(MenuProduct.cafe_id == TEST_CAFE_ID)
        )
    }

    first, variations, _, _, _ = _seeded_rows(database_session, sales_catalog)
    second = seed_sales_catalog(database_session, sales_catalog)

    assert len(products_before) == 18
    assert first == SalesCatalogSeedResult(30, 2, 7, 13)
    assert second == SalesCatalogSeedResult(0, 0, 0, 0)
    assert len(variations) == 30
    assert {
        product.product_id: (product.name, product.category, product.price_cents)
        for product in database_session.scalars(
            select(MenuProduct).where(MenuProduct.cafe_id == TEST_CAFE_ID)
        )
    } == products_before

    by_local_id = {
        variation.variation_id.removeprefix(f"{TEST_CAFE_ID}:"): variation
        for variation in variations
    }
    latte_regular = by_local_id["COF003_REG"]
    latte_large = by_local_id["COF003_LRG"]
    assert (latte_regular.name, latte_regular.size_oz, latte_regular.price_cents) == (
        "Regular 12 oz",
        12,
        500,
    )
    assert (latte_large.name, latte_large.size_oz, latte_large.price_cents) == (
        "Large 16 oz",
        16,
        575,
    )
    bakery_variations = [
        variation
        for local_id, variation in by_local_id.items()
        if local_id.split("_", 1)[0] in BAKERY_PRODUCT_IDS
    ]
    assert len(bakery_variations) == 6
    assert all(
        variation.code == "REG"
        and variation.name == "Regular"
        and variation.size_oz is None
        for variation in bakery_variations
    )


@pytest.mark.database
def test_seed_creates_approved_modifier_groups_modifiers_and_assignments(
    database_session: Session,
    sales_catalog: SalesSeedCatalog,
) -> None:
    _, _, groups, modifiers, mappings = _seeded_rows(database_session, sales_catalog)
    group_by_local_id = {
        group.group_id.removeprefix(f"{TEST_CAFE_ID}:"): group for group in groups
    }
    modifier_by_local_id = {
        modifier.modifier_id.removeprefix(f"{TEST_CAFE_ID}:"): modifier
        for modifier in modifiers
    }

    assert {
        local_id: (group.name, group.selection_type, group.min_select, group.max_select)
        for local_id, group in group_by_local_id.items()
    } == {
        "MILK": ("Milk", "single", 1, 1),
        "EXTRAS": ("Extras", "multiple", 0, 3),
    }
    assert {
        local_id: (modifier.name, modifier.price_cents, modifier.is_default)
        for local_id, modifier in modifier_by_local_id.items()
    } == {
        "MILK_WHOLE": ("Whole milk", 0, True),
        "MILK_SKIM": ("Skim milk", 0, False),
        "MILK_OAT": ("Oat milk", 75, False),
        "MILK_ALMOND": ("Almond milk", 75, False),
        "EXTRA_SHOT": ("Extra shot", 100, False),
        "EXTRA_VANILLA": ("Vanilla", 50, False),
        "EXTRA_CARAMEL": ("Caramel", 50, False),
    }
    assert sum(modifier.is_default for modifier in modifiers) == 1
    milk_group_id = catalog_identifier(TEST_CAFE_ID, "MILK")
    extras_group_id = catalog_identifier(TEST_CAFE_ID, "EXTRAS")
    assert {
        mapping.product_id.removeprefix(f"{TEST_CAFE_ID}:")
        for mapping in mappings
        if mapping.group_id == milk_group_id
    } == MILK_PRODUCT_IDS
    assert {
        mapping.product_id.removeprefix(f"{TEST_CAFE_ID}:")
        for mapping in mappings
        if mapping.group_id == extras_group_id
    } == EXTRAS_PRODUCT_IDS


@pytest.mark.database
def test_database_rejects_invalid_prices_and_selection_limits(
    database_session: Session,
    sales_catalog: SalesSeedCatalog,
) -> None:
    product_id = sales_catalog.products[0].product_id
    _expect_integrity_error(
        database_session,
        ProductVariation(
            variation_id=catalog_identifier(TEST_CAFE_ID, "INVALID_PRICE_REG"),
            cafe_id=TEST_CAFE_ID,
            product_id=product_id,
            code="REG",
            name="Invalid",
            size_oz=12,
            price_cents=-1,
        ),
    )
    for index, values in enumerate(
        (
            ("invalid", 0, 1),
            ("single", -1, 1),
            ("single", 0, 0),
            ("multiple", 3, 2),
        )
    ):
        selection_type, min_select, max_select = values
        _expect_integrity_error(
            database_session,
            ModifierGroup(
                group_id=catalog_identifier(TEST_CAFE_ID, f"INVALID_GROUP_{index}"),
                cafe_id=TEST_CAFE_ID,
                name=f"Invalid {index}",
                selection_type=selection_type,
                min_select=min_select,
                max_select=max_select,
            ),
        )

    seed_sales_catalog(database_session, sales_catalog)
    _expect_integrity_error(
        database_session,
        Modifier(
            modifier_id=catalog_identifier(TEST_CAFE_ID, "INVALID_MODIFIER"),
            cafe_id=TEST_CAFE_ID,
            group_id=catalog_identifier(TEST_CAFE_ID, "EXTRAS"),
            name="Invalid",
            price_cents=-1,
        ),
    )


@pytest.mark.database
def test_duplicate_variation_codes_and_cross_cafe_references_are_rejected(
    database_session: Session,
    sales_catalog: SalesSeedCatalog,
) -> None:
    seed_sales_catalog(database_session, sales_catalog)
    product_id = sales_catalog.products[0].product_id
    _expect_integrity_error(
        database_session,
        ProductVariation(
            variation_id=catalog_identifier(TEST_CAFE_ID, "DUPLICATE_REG"),
            cafe_id=TEST_CAFE_ID,
            product_id=product_id,
            code="REG",
            name="Duplicate",
            size_oz=12,
            price_cents=1,
        ),
    )

    database_session.add(
        Cafe(cafe_id=OTHER_CAFE_ID, name="Other Catalog Test", timezone="UTC")
    )
    database_session.flush()
    _expect_integrity_error(
        database_session,
        ProductVariation(
            variation_id=catalog_identifier(OTHER_CAFE_ID, "CROSS_CAFE_REG"),
            cafe_id=OTHER_CAFE_ID,
            product_id=product_id,
            code="REG",
            name="Cross café",
            size_oz=12,
            price_cents=1,
        ),
    )


@pytest.mark.database
def test_variations_support_inactive_history(
    database_session: Session,
    sales_catalog: SalesSeedCatalog,
) -> None:
    seed_sales_catalog(database_session, sales_catalog)
    variation_id = catalog_identifier(TEST_CAFE_ID, "COF003_REG")
    variation = database_session.get(ProductVariation, variation_id)
    variation.active = False
    database_session.flush()

    assert database_session.get(ProductVariation, variation_id).active is False


@pytest.mark.database
def test_conflicting_seed_rolls_back_all_new_catalog_rows(
    database_session: Session,
    sales_catalog: SalesSeedCatalog,
) -> None:
    conflicting_variation = ProductVariation(
        variation_id=catalog_identifier(TEST_CAFE_ID, "COF005_REG"),
        cafe_id=TEST_CAFE_ID,
        product_id=catalog_identifier(TEST_CAFE_ID, "COF005"),
        code="REG",
        name="Conflicting Regular",
        size_oz=12,
        price_cents=1,
    )
    database_session.add(conflicting_variation)
    database_session.flush()

    with pytest.raises(SalesCatalogSeedError, match="Conflicting existing variation"):
        seed_sales_catalog(database_session, sales_catalog)

    assert database_session.scalar(
        select(func.count())
        .select_from(ProductVariation)
        .where(ProductVariation.cafe_id == TEST_CAFE_ID)
    ) == 1
    assert database_session.scalar(
        select(func.count())
        .select_from(ModifierGroup)
        .where(ModifierGroup.cafe_id == TEST_CAFE_ID)
    ) == 0
    assert database_session.scalar(
        select(func.count())
        .select_from(Modifier)
        .where(Modifier.cafe_id == TEST_CAFE_ID)
    ) == 0
    assert database_session.scalar(
        select(func.count())
        .select_from(ProductModifierGroup)
        .where(ProductModifierGroup.cafe_id == TEST_CAFE_ID)
    ) == 0


def test_demo_plan_uses_approved_identifiers_and_counts() -> None:
    plan = build_sales_catalog_plan(load_sales_catalog())

    assert len(plan.variations) == 30
    assert len(plan.groups) == 2
    assert len(plan.modifiers) == 7
    assert len(plan.mappings) == 13
    assert {variation["variation_id"] for variation in plan.variations} >= {
        "COF003_REG",
        "COF003_LRG",
        "BAK001_REG",
    }
    assert {group["group_id"] for group in plan.groups} == {"MILK", "EXTRAS"}
