import os
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from backend.db.config import get_settings
from backend.db.session import engine

M1_REVISION = "a6f3c2d91b47"
M2_REVISION = "d4e8f1a2c6b9"
HEAD_REVISION = "f7a3c9e4b812"
CAFE_ID = "TEST_M2_MIGRATION_CAFE"
PRODUCT_ID = "TEST_M2_MIGRATION_PRODUCT"
SALE_ID = "DEMO_SALE_M2_MIGRATION"
SALE_ITEM_ID = "TEST_M2_MIGRATION_ITEM"
SOURCE_ID = "DEMO_POS_M2_MIGRATION"
SOLD_AT = datetime(2025, 1, 8, 17, 30, tzinfo=timezone.utc)
RECORDED_AT = datetime(2025, 1, 8, 17, 31, tzinfo=timezone.utc)


def _require_disposable_database() -> None:
    if os.environ.get("CAFEPILOT_DISPOSABLE_MIGRATION_TEST") != "1":
        pytest.skip("destructive migration test requires a disposable database")
    if "test" not in get_settings().postgres_db.lower():
        pytest.fail("migration test database name must clearly identify a test database")


def _legacy_snapshot() -> tuple:
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT sale.sale_id, item.sale_item_id, item.quantity, "
                "sale.sold_at, sale.recorded_at, item.unit_price_cents, "
                "item.line_total_cents, sale.total_cents "
                "FROM sales AS sale "
                "JOIN sale_items AS item ON item.sale_id = sale.sale_id "
                "WHERE sale.sale_id = :sale_id"
            ),
            {"sale_id": SALE_ID},
        ).one()


@pytest.mark.database
def test_m2_upgrade_downgrade_and_reupgrade_preserve_v1_history() -> None:
    _require_disposable_database()
    migration_config = Config("alembic.ini")
    engine.dispose()
    command.downgrade(migration_config, M1_REVISION)
    engine.dispose()

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO cafes (cafe_id, name, timezone) "
                "VALUES (:cafe_id, 'M2 Migration Test', 'UTC')"
            ),
            {"cafe_id": CAFE_ID},
        )
        connection.execute(
            text(
                "INSERT INTO menu_products "
                "(product_id, cafe_id, name, category, price_cents) "
                "VALUES (:product_id, :cafe_id, 'Historical Latte', 'coffee', 525)"
            ),
            {"product_id": PRODUCT_ID, "cafe_id": CAFE_ID},
        )
        connection.execute(
            text(
                "INSERT INTO sales "
                "(sale_id, cafe_id, source_id, sold_at, recorded_at, status, total_cents) "
                "VALUES (:sale_id, :cafe_id, :source_id, :sold_at, :recorded_at, "
                "'completed', 1050)"
            ),
            {
                "sale_id": SALE_ID,
                "cafe_id": CAFE_ID,
                "source_id": SOURCE_ID,
                "sold_at": SOLD_AT,
                "recorded_at": RECORDED_AT,
            },
        )
        connection.execute(
            text(
                "INSERT INTO sale_items "
                "(sale_item_id, sale_id, cafe_id, product_id, quantity, "
                "unit_price_cents, line_total_cents) "
                "VALUES (:item_id, :sale_id, :cafe_id, :product_id, 2, 525, 1050)"
            ),
            {
                "item_id": SALE_ITEM_ID,
                "sale_id": SALE_ID,
                "cafe_id": CAFE_ID,
                "product_id": PRODUCT_ID,
            },
        )

    before = _legacy_snapshot()
    assert before == (
        SALE_ID,
        SALE_ITEM_ID,
        2,
        SOLD_AT,
        RECORDED_AT,
        525,
        1050,
        1050,
    )

    try:
        command.upgrade(migration_config, M2_REVISION)
        engine.dispose()
        assert _legacy_snapshot() == before
        with engine.connect() as connection:
            sale = connection.execute(
                text(
                    "SELECT source, external_id, subtotal_cents, discount_cents, "
                    "tax_cents, total_cents FROM sales WHERE sale_id = :sale_id"
                ),
                {"sale_id": SALE_ID},
            ).one()
            item = connection.execute(
                text(
                    "SELECT variation_id, product_name, variation_name, "
                    "modifier_unit_cents FROM sale_items WHERE sale_item_id = :item_id"
                ),
                {"item_id": SALE_ITEM_ID},
            ).one()
            modifier_count = connection.scalar(
                text(
                    "SELECT count(*) FROM sale_item_modifiers "
                    "WHERE sale_item_id = :item_id"
                ),
                {"item_id": SALE_ITEM_ID},
            )

        assert sale == ("generated", SOURCE_ID, 1050, 0, 0, 1050)
        assert item == (f"{PRODUCT_ID}_REG", "Historical Latte", "Regular", 0)
        assert modifier_count == 0

        command.downgrade(migration_config, M1_REVISION)
        engine.dispose()
        assert _legacy_snapshot() == before

        command.upgrade(migration_config, M2_REVISION)
        engine.dispose()
        assert _legacy_snapshot() == before
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                M2_REVISION
            )
    finally:
        command.upgrade(migration_config, HEAD_REVISION)
        engine.dispose()
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM sale_item_modifiers WHERE sale_item_id = :item_id"),
                {"item_id": SALE_ITEM_ID},
            )
            connection.execute(
                text("DELETE FROM sale_items WHERE sale_item_id = :item_id"),
                {"item_id": SALE_ITEM_ID},
            )
            connection.execute(
                text("DELETE FROM sales WHERE sale_id = :sale_id"),
                {"sale_id": SALE_ID},
            )
            connection.execute(
                text("DELETE FROM product_variations WHERE product_id = :product_id"),
                {"product_id": PRODUCT_ID},
            )
            connection.execute(
                text("DELETE FROM menu_products WHERE product_id = :product_id"),
                {"product_id": PRODUCT_ID},
            )
            connection.execute(
                text("DELETE FROM cafes WHERE cafe_id = :cafe_id"),
                {"cafe_id": CAFE_ID},
            )
