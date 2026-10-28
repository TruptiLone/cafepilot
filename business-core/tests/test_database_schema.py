from sqlalchemy import DateTime, Numeric, inspect, text
import pytest

from backend.db.session import engine

APPLICATION_TABLES = {
    "cafes",
    "employees",
    "inventory_items",
    "menu_products",
    "modifier_groups",
    "modifiers",
    "product_modifier_groups",
    "product_variations",
    "sale_items",
    "sales",
    "shifts",
    "stock_counts",
    "stock_count_lines",
    "inventory_movements",
}
REVISION = "a6f3c2d91b47"

pytestmark = pytest.mark.database


def _foreign_keys_by_name(table_name: str) -> dict[str, dict]:
    return {
        foreign_key["name"]: foreign_key
        for foreign_key in inspect(engine).get_foreign_keys(table_name, schema="public")
    }


def test_application_schema_is_installed_at_head() -> None:
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


def test_sales_catalog_schema_has_expected_constraints_and_indexes() -> None:
    inspector = inspect(engine)
    variation_checks = {
        constraint["name"]
        for constraint in inspector.get_check_constraints(
            "product_variations",
            schema="public",
        )
    }
    group_checks = {
        constraint["name"]
        for constraint in inspector.get_check_constraints(
            "modifier_groups",
            schema="public",
        )
    }
    modifier_checks = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("modifiers", schema="public")
    }
    variation_foreign_keys = _foreign_keys_by_name("product_variations")
    modifier_foreign_keys = _foreign_keys_by_name("modifiers")
    mapping_foreign_keys = _foreign_keys_by_name("product_modifier_groups")
    variation_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(
            "product_variations",
            schema="public",
        )
    }
    mapping_uniques = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(
            "product_modifier_groups",
            schema="public",
        )
    }
    modifier_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("modifiers", schema="public")
    }

    assert {
        "ck_product_variations_code",
        "ck_product_variations_size_positive",
        "ck_product_variations_price_nonnegative",
    }.issubset(variation_checks)
    assert {
        "ck_modifier_groups_selection_type",
        "ck_modifier_groups_min_select_nonnegative",
        "ck_modifier_groups_max_select_positive",
        "ck_modifier_groups_selection_range",
    }.issubset(group_checks)
    assert "ck_modifiers_price_nonnegative" in modifier_checks
    assert variation_foreign_keys["fk_product_variations_product_cafe"][
        "constrained_columns"
    ] == ["product_id", "cafe_id"]
    assert modifier_foreign_keys["fk_modifiers_group_cafe"][
        "constrained_columns"
    ] == ["group_id", "cafe_id"]
    assert mapping_foreign_keys["fk_product_modifier_groups_product_cafe"][
        "constrained_columns"
    ] == ["product_id", "cafe_id"]
    assert mapping_foreign_keys["fk_product_modifier_groups_group_cafe"][
        "constrained_columns"
    ] == ["group_id", "cafe_id"]
    assert ("cafe_id", "product_id", "code") in variation_uniques
    assert ("cafe_id", "product_id", "group_id") in mapping_uniques
    assert modifier_indexes["uq_modifiers_cafe_group_default"]["unique"]


def test_staffing_schema_has_expected_constraints_and_indexes() -> None:
    employee_columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("employees", schema="public")
    }
    shift_columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("shifts", schema="public")
    }
    shift_checks = {
        constraint["name"]
        for constraint in inspect(engine).get_check_constraints("shifts", schema="public")
    }
    shift_foreign_keys = _foreign_keys_by_name("shifts")

    assert employee_columns["active"]["default"] == "true"
    assert employee_columns["created_at"]["type"].timezone
    assert shift_columns["start_at"]["type"].timezone
    assert shift_columns["end_at"]["type"].timezone
    assert shift_columns["created_at"]["type"].timezone
    assert {"ck_shifts_start_before_end", "ck_shifts_status"}.issubset(shift_checks)
    assert shift_foreign_keys["fk_shifts_employee_cafe"]["constrained_columns"] == [
        "employee_id",
        "cafe_id",
    ]
    assert "ix_employees_cafe_id_active" in {
        index["name"]
        for index in inspect(engine).get_indexes("employees", schema="public")
    }
    assert "ix_shifts_cafe_id_start_at" in {
        index["name"]
        for index in inspect(engine).get_indexes("shifts", schema="public")
    }

    with engine.connect() as connection:
        exclusion_definition = connection.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'excl_shifts_employee_scheduled_overlap'"
            )
        ).scalar_one()
        assert connection.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'btree_gist'")
        ).scalar_one() == 1

    assert "EXCLUDE USING gist" in exclusion_definition
    assert "employee_id WITH =" in exclusion_definition
    assert "tstzrange(start_at, end_at" in exclusion_definition
    assert "status = 'scheduled'" in exclusion_definition
