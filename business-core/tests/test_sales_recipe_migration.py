import os
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from backend.db.config import get_settings
from backend.db.session import engine

M2_REVISION = "d4e8f1a2c6b9"
M3_REVISION = "f7a3c9e4b812"


@pytest.mark.database
def test_m3_upgrade_downgrade_and_reupgrade_preserve_m2_sales() -> None:
    if os.environ.get("CAFEPILOT_DISPOSABLE_MIGRATION_TEST") != "1":
        pytest.skip("destructive migration test requires a disposable database")
    if "test" not in get_settings().postgres_db.lower():
        pytest.fail("migration test database name must clearly identify a test database")

    config = Config("alembic.ini")
    engine.dispose()
    command.downgrade(config, M2_REVISION)
    engine.dispose()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO cafes (cafe_id, name, timezone, tax_rate_bps) "
                "VALUES ('TEST_M3_MIGRATION_CAFE', 'M3 Test', 'UTC', 0)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO menu_products "
                "(product_id, cafe_id, name, category, price_cents) VALUES "
                "('TEST_M3_PRODUCT', 'TEST_M3_MIGRATION_CAFE', 'M3 Product', 'test', 100)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO product_variations "
                "(variation_id, cafe_id, product_id, code, name, price_cents) VALUES "
                "('TEST_M3_VARIATION', 'TEST_M3_MIGRATION_CAFE', "
                "'TEST_M3_PRODUCT', 'REG', 'Regular', 100)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO sales "
                "(sale_id, cafe_id, source_id, source, sold_at, recorded_at, status, "
                "subtotal_cents, discount_cents, tax_cents, total_cents) VALUES "
                "('TEST_M3_SALE', 'TEST_M3_MIGRATION_CAFE', 'TEST_M3_SOURCE', 'pos', "
                ":at, :at, 'completed', 100, 0, 0, 100)"
            ),
            {"at": datetime(2025, 1, 1, tzinfo=timezone.utc)},
        )
        connection.execute(
            text(
                "INSERT INTO sale_items "
                "(sale_item_id, sale_id, cafe_id, product_id, variation_id, "
                "product_name, variation_name, quantity, unit_price_cents, "
                "modifier_unit_cents, line_total_cents) VALUES "
                "('TEST_M3_ITEM', 'TEST_M3_SALE', 'TEST_M3_MIGRATION_CAFE', "
                "'TEST_M3_PRODUCT', 'TEST_M3_VARIATION', 'M3 Product', 'Regular', "
                "1, 100, 0, 100)"
            )
        )

    try:
        command.upgrade(config, M3_REVISION)
        engine.dispose()
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert {"recipe_lines", "modifier_recipe_effects"}.issubset(tables)
            assert connection.scalar(
                text("SELECT count(*) FROM sale_items WHERE sale_item_id='TEST_M3_ITEM'")
            ) == 1

        command.downgrade(config, M2_REVISION)
        engine.dispose()
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            assert "recipe_lines" not in tables
            assert "modifier_recipe_effects" not in tables
            assert connection.scalar(
                text("SELECT count(*) FROM sale_items WHERE sale_item_id='TEST_M3_ITEM'")
            ) == 1

        command.upgrade(config, M3_REVISION)
        engine.dispose()
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                M3_REVISION
            )
    finally:
        command.upgrade(config, M3_REVISION)
        engine.dispose()
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM sale_items WHERE sale_item_id='TEST_M3_ITEM'"))
            connection.execute(text("DELETE FROM sales WHERE sale_id='TEST_M3_SALE'"))
            connection.execute(
                text("DELETE FROM product_variations WHERE variation_id='TEST_M3_VARIATION'")
            )
            connection.execute(
                text("DELETE FROM menu_products WHERE product_id='TEST_M3_PRODUCT'")
            )
            connection.execute(
                text("DELETE FROM cafes WHERE cafe_id='TEST_M3_MIGRATION_CAFE'")
            )
