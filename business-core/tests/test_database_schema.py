from sqlalchemy import DateTime, Numeric, inspect, text
import pytest

from backend.db.session import engine

APPLICATION_TABLES = {
    "cafes",
    "inventory_items",
    "menu_products",
    "sale_items",
    "sales",
    "stock_counts",
    "stock_count_lines",
    "inventory_movements",
}
REVISION = "76fdeb7269e5"

pytestmark = pytest.mark.database


def _foreign_keys_by_name(table_name: str) -> dict[str, dict]:
    return {
        foreign_key["name"]: foreign_key
        for foreign_key in inspect(engine).get_foreign_keys(table_name, schema="public")
    }


def test_initial_inventory_schema_is_installed_at_head() -> None:
    tables = set(inspect(engine).get_table_names(schema="public"))

    assert tables == APPLICATION_TABLES | {"alembic_version"}

    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == REVISION


def test_schema_has_expected_types_and_timezone_columns() -> None:
    inventory_columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("inventory_items", schema="public")
    }
    count_columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("stock_counts", schema="public")
    }
    movement_columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("inventory_movements", schema="public")
    }

    assert isinstance(inventory_columns["reorder_point"]["type"], Numeric)
    assert inventory_columns["reorder_point"]["type"].precision == 18
    assert inventory_columns["reorder_point"]["type"].scale == 3
    assert isinstance(inventory_columns["par_level"]["type"], Numeric)
    assert inventory_columns["par_level"]["type"].precision == 18
    assert inventory_columns["par_level"]["type"].scale == 3
    assert isinstance(movement_columns["quantity_delta"]["type"], Numeric)
    assert movement_columns["quantity_delta"]["type"].precision == 18
    assert movement_columns["quantity_delta"]["type"].scale == 3
    assert isinstance(count_columns["counted_at"]["type"], DateTime)
    assert count_columns["counted_at"]["type"].timezone
    assert count_columns["recorded_at"]["type"].timezone
    assert movement_columns["occurred_at"]["type"].timezone
    assert movement_columns["recorded_at"]["type"].timezone
    assert inventory_columns["is_perishable"]["default"] == "false"
    assert inventory_columns["active"]["default"] == "true"


def test_schema_has_cafe_scoped_foreign_keys_and_inventory_constraints() -> None:
    line_foreign_keys = _foreign_keys_by_name("stock_count_lines")
    movement_foreign_keys = _foreign_keys_by_name("inventory_movements")
    line_checks = {
        constraint["name"]
        for constraint in inspect(engine).get_check_constraints(
            "stock_count_lines", schema="public"
        )
    }
    count_checks = {
        constraint["name"]
        for constraint in inspect(engine).get_check_constraints(
            "stock_counts", schema="public"
        )
    }
    movement_checks = {
        constraint["name"]
        for constraint in inspect(engine).get_check_constraints(
            "inventory_movements", schema="public"
        )
    }
    movement_unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspect(engine).get_unique_constraints(
            "inventory_movements", schema="public"
        )
    }
    item_unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspect(engine).get_unique_constraints(
            "inventory_items", schema="public"
        )
    }
    line_unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspect(engine).get_unique_constraints(
            "stock_count_lines", schema="public"
        )
    }
    indexes = {
        table_name: {
            index["name"]
            for index in inspect(engine).get_indexes(table_name, schema="public")
        }
        for table_name in APPLICATION_TABLES
    }

    assert line_foreign_keys["fk_stock_count_lines_count_cafe"][
        "constrained_columns"
    ] == ["count_id", "cafe_id"]
    assert line_foreign_keys["fk_stock_count_lines_item_cafe"][
        "constrained_columns"
    ] == ["ingredient_id", "cafe_id"]
    assert movement_foreign_keys["fk_inventory_movements_item_cafe"][
        "constrained_columns"
    ] == ["ingredient_id", "cafe_id"]
    assert "ck_stock_count_lines_counted_quantity_nonnegative" in line_checks
    assert "ck_stock_counts_verified_requires_counted_at" in count_checks
    assert "ck_inventory_movements_type_and_quantity_sign" in movement_checks
    assert (
        "ingredient_id",
        "source_type",
        "source_id",
    ) in movement_unique_constraints
    assert ("ingredient_id", "cafe_id") in item_unique_constraints
    assert ("count_id", "ingredient_id") in line_unique_constraints
    assert "ix_inventory_items_cafe_id_active" in indexes["inventory_items"]
    assert "ix_stock_counts_cafe_id_counted_at" in indexes["stock_counts"]
    assert "ix_inventory_movements_ingredient_id_occurred_at" in indexes[
        "inventory_movements"
    ]


def test_sales_schema_has_expected_constraints_and_indexes() -> None:
    product_checks = {
        constraint["name"]
        for constraint in inspect(engine).get_check_constraints(
            "menu_products", schema="public"
        )
    }
    sale_checks = {
        constraint["name"]
        for constraint in inspect(engine).get_check_constraints("sales", schema="public")
    }
    item_checks = {
        constraint["name"]
        for constraint in inspect(engine).get_check_constraints(
            "sale_items", schema="public"
        )
    }
    item_foreign_keys = _foreign_keys_by_name("sale_items")
    sale_unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspect(engine).get_unique_constraints("sales", schema="public")
    }
    item_unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspect(engine).get_unique_constraints(
            "sale_items", schema="public"
        )
    }

    assert "ck_menu_products_price_nonnegative" in product_checks
    assert {"ck_sales_status", "ck_sales_total_nonnegative"}.issubset(sale_checks)
    assert {
        "ck_sale_items_quantity_positive",
        "ck_sale_items_unit_price_nonnegative",
        "ck_sale_items_line_total_nonnegative",
        "ck_sale_items_line_total_matches",
    }.issubset(item_checks)
    assert item_foreign_keys["fk_sale_items_sale_cafe"]["constrained_columns"] == [
        "sale_id",
        "cafe_id",
    ]
    assert item_foreign_keys["fk_sale_items_product_cafe"]["constrained_columns"] == [
        "product_id",
        "cafe_id",
    ]
    assert ("cafe_id", "source_id") in sale_unique_constraints
    assert ("sale_id", "product_id") in item_unique_constraints
    assert "ix_menu_products_cafe_id_active" in {
        index["name"]
        for index in inspect(engine).get_indexes("menu_products", schema="public")
    }
    assert "ix_sales_cafe_id_sold_at" in {
        index["name"]
        for index in inspect(engine).get_indexes("sales", schema="public")
    }
