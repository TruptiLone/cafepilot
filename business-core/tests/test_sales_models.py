from sqlalchemy import Boolean, DateTime, Integer, Text, UniqueConstraint, inspect

from backend.db.base import Base
from backend.models.cafe import Cafe
from backend.modules.inventory.models import InventoryItem, InventoryMovement, StockCount
from backend.modules.sales.models import MenuProduct, Sale, SaleItem


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


def test_sales_tables_are_registered_in_shared_metadata() -> None:
    assert {"cafes", "menu_products", "sales", "sale_items"}.issubset(
        Base.metadata.tables
    )


def test_sales_columns_constraints_and_indexes_are_defined() -> None:
    products = Base.metadata.tables["menu_products"]
    sales = Base.metadata.tables["sales"]
    items = Base.metadata.tables["sale_items"]

    assert isinstance(products.c.product_id.type, Text)
    assert isinstance(products.c.price_cents.type, Integer)
    assert isinstance(products.c.active.type, Boolean)
    assert isinstance(sales.c.sold_at.type, DateTime)
    assert sales.c.sold_at.type.timezone
    assert sales.c.recorded_at.type.timezone
    assert products.primary_key.columns.keys() == ["product_id"]
    assert sales.primary_key.columns.keys() == ["sale_id"]
    assert items.primary_key.columns.keys() == ["sale_item_id"]
    assert ("product_id", "cafe_id") in _constraint_columns(
        products, UniqueConstraint
    )
    assert ("cafe_id", "source_id") in _constraint_columns(sales, UniqueConstraint)
    assert ("sale_id", "cafe_id") in _constraint_columns(sales, UniqueConstraint)
    assert ("sale_id", "product_id") in _constraint_columns(
        items, UniqueConstraint
    )
    assert {
        "fk_sale_items_sale_cafe",
        "fk_sale_items_product_cafe",
    } == {constraint.name for constraint in items.foreign_key_constraints}
    assert "price_cents >= 0" in _check_constraint_sql(products)
    assert "quantity > 0" in _check_constraint_sql(items)
    assert "line_total_cents = quantity * unit_price_cents" in _check_constraint_sql(
        items
    )
    assert "ix_menu_products_cafe_id_active" in {
        index.name for index in products.indexes
    }
    assert "ix_sales_cafe_id_sold_at" in {index.name for index in sales.indexes}


def test_sales_relationships_configure_without_overlap_warnings() -> None:
    assert inspect(Cafe).relationships["menu_products"].mapper.class_ is MenuProduct
    assert inspect(Cafe).relationships["sales"].mapper.class_ is Sale
    assert inspect(MenuProduct).relationships["sale_items"].mapper.class_ is SaleItem
    assert inspect(Sale).relationships["items"].mapper.class_ is SaleItem
    assert inspect(SaleItem).relationships["sale"].mapper.class_ is Sale
    assert inspect(SaleItem).relationships["product"].mapper.class_ is MenuProduct
