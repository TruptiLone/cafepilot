import os
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from backend.db.config import get_settings
from backend.db.session import engine

M1_TABLES = {
    "product_variations",
    "modifier_groups",
    "modifiers",
    "product_modifier_groups",
}
M1_REVISION = "a6f3c2d91b47"
HEAD_REVISION = "d4e8f1a2c6b9"
PREVIOUS_REVISION = "3411712f6af3"


@pytest.mark.database
def test_m1_upgrade_and_downgrade_preserve_v1_on_disposable_database() -> None:
    if os.environ.get("CAFEPILOT_DISPOSABLE_MIGRATION_TEST") != "1":
        pytest.skip("destructive migration test requires a disposable database")
    if "test" not in get_settings().postgres_db.lower():
        pytest.fail("migration test database name must clearly identify a test database")

    migration_config = Config("alembic.ini")
    engine.dispose()
    command.downgrade(migration_config, M1_REVISION)
    engine.dispose()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO cafes (cafe_id, name, timezone) "
                "VALUES ('TEST_M1_MIGRATION_CAFE', 'Migration Test', 'UTC')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO menu_products "
                "(product_id, cafe_id, name, category, price_cents) VALUES "
                "('TEST_M1_MIGRATION_PRODUCT', 'TEST_M1_MIGRATION_CAFE', "
                "'Migration Product', 'test', 100)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO sales "
                "(sale_id, cafe_id, source_id, sold_at, recorded_at, status, total_cents) "
                "VALUES ('TEST_M1_MIGRATION_SALE', 'TEST_M1_MIGRATION_CAFE', "
                "'TEST_M1_SOURCE', :timestamp, :timestamp, 'completed', 100)"
            ),
            {"timestamp": datetime(2025, 1, 1, tzinfo=timezone.utc)},
        )
        connection.execute(
            text(
                "INSERT INTO sale_items "
                "(sale_item_id, sale_id, cafe_id, product_id, quantity, "
                "unit_price_cents, line_total_cents) VALUES "
                "('TEST_M1_MIGRATION_ITEM', 'TEST_M1_MIGRATION_SALE', "
                "'TEST_M1_MIGRATION_CAFE', 'TEST_M1_MIGRATION_PRODUCT', 1, 100, 100)"
            )
        )

    try:
        command.downgrade(migration_config, PREVIOUS_REVISION)
        engine.dispose()
        with engine.connect() as connection:
            assert not M1_TABLES.intersection(inspect(connection).get_table_names())
            assert connection.scalar(
                text("SELECT count(*) FROM sales WHERE sale_id = 'TEST_M1_MIGRATION_SALE'")
            ) == 1
            assert connection.scalar(
                text(
                    "SELECT count(*) FROM sale_items "
                    "WHERE sale_item_id = 'TEST_M1_MIGRATION_ITEM'"
                )
            ) == 1

        command.upgrade(migration_config, M1_REVISION)
        engine.dispose()
        with engine.connect() as connection:
            assert M1_TABLES.issubset(inspect(connection).get_table_names())
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                M1_REVISION
            )
    finally:
        command.upgrade(migration_config, HEAD_REVISION)
        engine.dispose()
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM sale_items WHERE sale_item_id = 'TEST_M1_MIGRATION_ITEM'")
            )
            connection.execute(
                text("DELETE FROM sales WHERE sale_id = 'TEST_M1_MIGRATION_SALE'")
            )
            connection.execute(
                text(
                    "DELETE FROM product_variations "
                    "WHERE product_id = 'TEST_M1_MIGRATION_PRODUCT'"
                )
            )
            connection.execute(
                text(
                    "DELETE FROM menu_products "
                    "WHERE product_id = 'TEST_M1_MIGRATION_PRODUCT'"
                )
            )
            connection.execute(
                text("DELETE FROM cafes WHERE cafe_id = 'TEST_M1_MIGRATION_CAFE'")
            )
