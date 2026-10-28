from datetime import datetime, timezone

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
    ProductVariation,
    Sale,
    SaleItem,
    SaleItemModifier,
)

CAFE_ID = "TEST_SNAPSHOT_CAFE"
OTHER_CAFE_ID = "TEST_SNAPSHOT_OTHER_CAFE"
SOLD_AT = datetime(2025, 1, 9, 16, tzinfo=timezone.utc)


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
def snapshot_catalog(database_session: Session) -> None:
    database_session.add_all(
        [
            Cafe(cafe_id=CAFE_ID, name="Snapshot Test", timezone="UTC"),
            Cafe(cafe_id=OTHER_CAFE_ID, name="Other Snapshot Test", timezone="UTC"),
        ]
    )
    database_session.flush()
    database_session.add_all(
        [
            MenuProduct(
                product_id="TEST_SNAPSHOT_PRODUCT",
                cafe_id=CAFE_ID,
                name="Snapshot Product",
                category="test",
                price_cents=500,
            ),
            MenuProduct(
                product_id="TEST_SNAPSHOT_OTHER_PRODUCT",
                cafe_id=OTHER_CAFE_ID,
                name="Other Product",
                category="test",
                price_cents=600,
            ),
            ModifierGroup(
                group_id="TEST_SNAPSHOT_GROUP",
                cafe_id=CAFE_ID,
                name="Snapshot Extras",
                selection_type="multiple",
                min_select=0,
                max_select=2,
            ),
            ModifierGroup(
                group_id="TEST_SNAPSHOT_OTHER_GROUP",
                cafe_id=OTHER_CAFE_ID,
                name="Other Extras",
                selection_type="multiple",
                min_select=0,
                max_select=2,
            ),
        ]
    )
    database_session.flush()
    database_session.add_all(
        [
            ProductVariation(
                variation_id="TEST_SNAPSHOT_PRODUCT_REG",
                cafe_id=CAFE_ID,
                product_id="TEST_SNAPSHOT_PRODUCT",
                code="REG",
                name="Regular",
                price_cents=500,
            ),
            ProductVariation(
                variation_id="TEST_SNAPSHOT_OTHER_PRODUCT_REG",
                cafe_id=OTHER_CAFE_ID,
                product_id="TEST_SNAPSHOT_OTHER_PRODUCT",
                code="REG",
                name="Regular",
                price_cents=600,
            ),
            Modifier(
                modifier_id="TEST_SNAPSHOT_MODIFIER",
                cafe_id=CAFE_ID,
                group_id="TEST_SNAPSHOT_GROUP",
                name="Snapshot Modifier",
                price_cents=50,
            ),
            Modifier(
                modifier_id="TEST_SNAPSHOT_OTHER_MODIFIER",
                cafe_id=OTHER_CAFE_ID,
                group_id="TEST_SNAPSHOT_OTHER_GROUP",
                name="Other Modifier",
                price_cents=75,
            ),
        ]
    )
    database_session.flush()


def _sale(
    sale_id: str,
    source_id: str,
    *,
    cafe_id: str = CAFE_ID,
    source: str = "pos",
    external_id: str | None = None,
    subtotal_cents: int = 500,
    discount_cents: int = 0,
    tax_cents: int = 0,
    total_cents: int = 500,
) -> Sale:
    return Sale(
        sale_id=sale_id,
        cafe_id=cafe_id,
        source_id=source_id,
        source=source,
        external_id=external_id,
        sold_at=SOLD_AT,
        recorded_at=SOLD_AT,
        status="completed",
        subtotal_cents=subtotal_cents,
        discount_cents=discount_cents,
        tax_cents=tax_cents,
        total_cents=total_cents,
    )


def _item(item_id: str, sale_id: str) -> SaleItem:
    return SaleItem(
        sale_item_id=item_id,
        sale_id=sale_id,
        cafe_id=CAFE_ID,
        product_id="TEST_SNAPSHOT_PRODUCT",
        variation_id="TEST_SNAPSHOT_PRODUCT_REG",
        product_name="Snapshot Product",
        variation_name="Regular",
        quantity=1,
        unit_price_cents=500,
        modifier_unit_cents=0,
        line_total_cents=500,
    )


def _expect_integrity_error(database_session: Session, record: object) -> None:
    with pytest.raises(IntegrityError):
        with database_session.begin_nested():
            database_session.add(record)
            database_session.flush()


@pytest.mark.database
def test_external_identity_is_unique_per_cafe_and_source(
    database_session: Session,
    snapshot_catalog: None,
) -> None:
    database_session.add(
        _sale("TEST_EXT_POS_1", "TEST_EXT_SOURCE_1", external_id="ORDER-1")
    )
    database_session.flush()

    _expect_integrity_error(
        database_session,
        _sale("TEST_EXT_POS_2", "TEST_EXT_SOURCE_2", external_id="ORDER-1"),
    )

    database_session.add(
        _sale(
            "TEST_EXT_GENERATED",
            "TEST_EXT_SOURCE_3",
            source="generated",
            external_id="ORDER-1",
        )
    )
    database_session.add(
        _sale(
            "TEST_EXT_OTHER_CAFE",
            "TEST_EXT_SOURCE_4",
            cafe_id=OTHER_CAFE_ID,
            external_id="ORDER-1",
        )
    )
    database_session.flush()


@pytest.mark.database
@pytest.mark.parametrize(
    ("amounts", "expected_constraint"),
    [
        ((-1, 0, 0, -1), "subtotal"),
        ((500, -1, 0, 501), "discount"),
        ((500, 501, 0, -1), "discount"),
        ((500, 0, -1, 499), "tax"),
        ((500, 100, 50, 500), "total"),
    ],
)
def test_invalid_sale_amounts_are_rejected(
    database_session: Session,
    snapshot_catalog: None,
    amounts: tuple[int, int, int, int],
    expected_constraint: str,
) -> None:
    subtotal, discount, tax, total = amounts
    _expect_integrity_error(
        database_session,
        _sale(
            f"TEST_INVALID_{expected_constraint}_{subtotal}_{discount}_{tax}",
            f"TEST_INVALID_SOURCE_{expected_constraint}_{subtotal}_{discount}_{tax}",
            subtotal_cents=subtotal,
            discount_cents=discount,
            tax_cents=tax,
            total_cents=total,
        ),
    )


@pytest.mark.database
def test_multiple_lines_for_same_product_are_allowed(
    database_session: Session,
    snapshot_catalog: None,
) -> None:
    sale = _sale(
        "TEST_MULTI_LINE_SALE",
        "TEST_MULTI_LINE_SOURCE",
        total_cents=1000,
        subtotal_cents=1000,
    )
    database_session.add(sale)
    database_session.flush()
    database_session.add_all(
        [
            _item("TEST_MULTI_LINE_ITEM_1", sale.sale_id),
            _item("TEST_MULTI_LINE_ITEM_2", sale.sale_id),
        ]
    )
    database_session.flush()

    assert database_session.scalar(
        select(func.count())
        .select_from(SaleItem)
        .where(SaleItem.sale_id == sale.sale_id)
    ) == 2


@pytest.mark.database
def test_modifier_aware_line_total_is_enforced(
    database_session: Session,
    snapshot_catalog: None,
) -> None:
    sale = _sale(
        "TEST_INVALID_LINE_TOTAL_SALE",
        "TEST_INVALID_LINE_TOTAL_SOURCE",
        subtotal_cents=550,
        total_cents=550,
    )
    database_session.add(sale)
    database_session.flush()
    item = _item("TEST_INVALID_LINE_TOTAL_ITEM", sale.sale_id)
    item.modifier_unit_cents = 50
    _expect_integrity_error(database_session, item)


@pytest.mark.database
def test_cross_cafe_variation_and_modifier_references_are_rejected(
    database_session: Session,
    snapshot_catalog: None,
) -> None:
    sale = _sale("TEST_CROSS_CAFE_SALE", "TEST_CROSS_CAFE_SOURCE")
    item = _item("TEST_CROSS_CAFE_ITEM", sale.sale_id)
    database_session.add_all([sale, item])
    database_session.flush()

    invalid_variation_item = _item("TEST_CROSS_CAFE_VARIATION_ITEM", sale.sale_id)
    invalid_variation_item.variation_id = "TEST_SNAPSHOT_OTHER_PRODUCT_REG"
    _expect_integrity_error(database_session, invalid_variation_item)

    _expect_integrity_error(
        database_session,
        SaleItemModifier(
            sale_item_modifier_id="TEST_CROSS_CAFE_MODIFIER_REF",
            cafe_id=CAFE_ID,
            sale_item_id=item.sale_item_id,
            modifier_id="TEST_SNAPSHOT_OTHER_MODIFIER",
            modifier_name="Other Modifier",
            price_cents=75,
            quantity=1,
        ),
    )
    _expect_integrity_error(
        database_session,
        SaleItemModifier(
            sale_item_modifier_id="TEST_CROSS_CAFE_ITEM_REF",
            cafe_id=OTHER_CAFE_ID,
            sale_item_id=item.sale_item_id,
            modifier_id="TEST_SNAPSHOT_OTHER_MODIFIER",
            modifier_name="Other Modifier",
            price_cents=75,
            quantity=1,
        ),
    )
