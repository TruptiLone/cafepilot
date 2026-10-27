from sqlalchemy import Boolean, DateTime, Numeric, Text, UniqueConstraint, inspect

from backend.db.base import Base
from backend.models.cafe import Cafe, INITIAL_CAFE_ID
from backend.modules.inventory.models import (
    InventoryItem,
    InventoryMovement,
    StockCount,
    StockCountLine,
)


def _constraint_columns(table, constraint_type):
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, constraint_type)
    }


def _check_constraint_sql(table) -> set[str]:
    return {
        str(constraint.sqltext)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }


def test_inventory_tables_are_registered_in_shared_metadata() -> None:
    assert {
        "cafes",
        "inventory_items",
        "stock_counts",
        "stock_count_lines",
        "inventory_movements",
    }.issubset(Base.metadata.tables)
    assert INITIAL_CAFE_ID == "CAFE001"


def test_inventory_columns_and_foreign_keys_are_defined() -> None:
    inventory_items = Base.metadata.tables["inventory_items"]
    stock_counts = Base.metadata.tables["stock_counts"]
    stock_count_lines = Base.metadata.tables["stock_count_lines"]
    inventory_movements = Base.metadata.tables["inventory_movements"]

    assert isinstance(inventory_items.c.ingredient_id.type, Text)
    assert isinstance(inventory_items.c.reorder_point.type, Numeric)
    assert inventory_items.c.reorder_point.type.precision == 18
    assert inventory_items.c.reorder_point.type.scale == 3
    assert isinstance(inventory_items.c.is_perishable.type, Boolean)
    assert isinstance(stock_counts.c.counted_at.type, DateTime)
    assert stock_counts.c.counted_at.type.timezone
    assert isinstance(stock_count_lines.c.counted_quantity.type, Numeric)
    assert isinstance(stock_count_lines.c.cafe_id.type, Text)
    assert isinstance(inventory_movements.c.quantity_delta.type, Numeric)
    assert inventory_items.primary_key.columns.keys() == ["ingredient_id"]
    assert stock_counts.primary_key.columns.keys() == ["count_id"]
    assert stock_count_lines.primary_key.columns.keys() == ["count_line_id"]
    assert inventory_movements.primary_key.columns.keys() == ["movement_id"]
    assert {foreign_key.target_fullname for foreign_key in inventory_items.foreign_keys} == {
        "cafes.cafe_id"
    }
    assert {foreign_key.target_fullname for foreign_key in stock_count_lines.foreign_keys} == {
        "inventory_items.ingredient_id",
        "inventory_items.cafe_id",
        "stock_counts.count_id",
        "stock_counts.cafe_id",
    }
    assert {
        "cafes.cafe_id",
        "inventory_items.ingredient_id",
        "inventory_items.cafe_id",
    }.issubset({
        foreign_key.target_fullname for foreign_key in inventory_movements.foreign_keys
    })


def test_inventory_constraints_and_indexes_are_defined() -> None:
    inventory_items = Base.metadata.tables["inventory_items"]
    stock_counts = Base.metadata.tables["stock_counts"]
    stock_count_lines = Base.metadata.tables["stock_count_lines"]
    inventory_movements = Base.metadata.tables["inventory_movements"]

    assert ("ingredient_id", "cafe_id") in _constraint_columns(
        inventory_items, UniqueConstraint
    )
    assert ("count_id", "ingredient_id") in _constraint_columns(
        stock_count_lines, UniqueConstraint
    )
    assert ("count_id", "cafe_id") in _constraint_columns(
        stock_counts, UniqueConstraint
    )
    assert ("ingredient_id", "source_type", "source_id") in _constraint_columns(
        inventory_movements, UniqueConstraint
    )
    assert any(
        "base_unit IN ('g', 'mL', 'each')" in check
        for check in _check_constraint_sql(inventory_items)
    )
    assert any(
        "counted_quantity >= 0" in check
        for check in _check_constraint_sql(stock_count_lines)
    )
    assert {
        "fk_stock_count_lines_count_cafe",
        "fk_stock_count_lines_item_cafe",
    }.issubset({
        constraint.name
        for constraint in stock_count_lines.foreign_key_constraints
    })
    assert "status <> 'verified' OR counted_at IS NOT NULL" in _check_constraint_sql(
        stock_counts
    )
    movement_check = next(
        check
        for check in _check_constraint_sql(inventory_movements)
        if "movement_type" in check
    )
    assert "movement_type = 'RECEIPT' AND quantity_delta > 0" in movement_check
    assert "movement_type IN ('WASTE', 'SALE_USAGE') AND quantity_delta < 0" in movement_check
    assert "movement_type = 'ADJUSTMENT' AND quantity_delta <> 0" in movement_check
    assert "ix_inventory_movements_ingredient_id_occurred_at" in {
        index.name for index in inventory_movements.indexes
    }


def test_inventory_relationships_are_configured() -> None:
    assert inspect(Cafe).relationships["inventory_items"].mapper.class_ is InventoryItem
    assert inspect(Cafe).relationships["stock_counts"].mapper.class_ is StockCount
    assert (
        inspect(InventoryItem).relationships["stock_count_lines"].mapper.class_
        is StockCountLine
    )
    assert (
        inspect(InventoryItem).relationships["inventory_movements"].mapper.class_
        is InventoryMovement
    )
    assert inspect(StockCount).relationships["lines"].mapper.class_ is StockCountLine
