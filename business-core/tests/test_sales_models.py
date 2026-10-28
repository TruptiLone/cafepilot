from sqlalchemy import Boolean, DateTime, Integer, Text, UniqueConstraint, inspect

from backend.db.base import Base
from backend.models.cafe import Cafe
from backend.modules.inventory.models import InventoryItem, InventoryMovement, StockCount
from backend.modules.sales.models import (
    MenuProduct,
    Modifier,
    ModifierGroup,
    ProductModifierGroup,
    ProductVariation,
    Sale,
    SaleItem,
    SaleItemModifier,
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


def test_sales_tables_are_registered_in_shared_metadata() -> None:
    assert {
        "cafes",
        "menu_products",
        "product_variations",
        "modifier_groups",
        "modifiers",
        "product_modifier_groups",
        "sales",
        "sale_items",
        "sale_item_modifiers",
    }.issubset(Base.metadata.tables)


def test_sales_columns_constraints_and_indexes_are_defined() -> None:
    products = Base.metadata.tables["menu_products"]
    sales = Base.metadata.tables["sales"]
    items = Base.metadata.tables["sale_items"]
    item_modifiers = Base.metadata.tables["sale_item_modifiers"]

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
    assert ("sale_item_id", "cafe_id") in _constraint_columns(
        items, UniqueConstraint
    )
    assert ("sale_id", "product_id") not in _constraint_columns(
        items, UniqueConstraint
    )
    assert {
        "fk_sale_items_sale_cafe",
        "fk_sale_items_product_cafe",
        "fk_sale_items_variation_cafe",
    } == {constraint.name for constraint in items.foreign_key_constraints}
    assert "price_cents >= 0" in _check_constraint_sql(products)
    assert "quantity > 0" in _check_constraint_sql(items)
    assert (
        "line_total_cents = quantity * (unit_price_cents + modifier_unit_cents)"
        in _check_constraint_sql(items)
    )
    assert "modifier_unit_cents >= 0" in _check_constraint_sql(items)
    assert {
        "fk_sale_item_modifiers_item_cafe",
        "fk_sale_item_modifiers_modifier_cafe",
        None,
    } == {
        constraint.name for constraint in item_modifiers.foreign_key_constraints
    }
    assert (
        "cafe_id",
        "sale_item_id",
        "modifier_id",
    ) in _constraint_columns(item_modifiers, UniqueConstraint)
    assert "price_cents >= 0" in _check_constraint_sql(item_modifiers)
    assert "quantity > 0" in _check_constraint_sql(item_modifiers)
    assert "ix_menu_products_cafe_id_active" in {
        index.name for index in products.indexes
    }
    assert "ix_sales_cafe_id_sold_at" in {index.name for index in sales.indexes}
    assert "uq_sales_cafe_source_external" in {
        index.name for index in sales.indexes
    }
    assert "ix_sale_items_cafe_id_sale_id_variation_id" in {
        index.name for index in items.indexes
    }
    assert "ix_sale_item_modifiers_cafe_id_sale_item_id" in {
        index.name for index in item_modifiers.indexes
    }


def test_sales_catalog_constraints_and_indexes_are_defined() -> None:
    variations = Base.metadata.tables["product_variations"]
    groups = Base.metadata.tables["modifier_groups"]
    modifiers = Base.metadata.tables["modifiers"]
    mappings = Base.metadata.tables["product_modifier_groups"]

    assert variations.primary_key.columns.keys() == ["variation_id"]
    assert groups.primary_key.columns.keys() == ["group_id"]
    assert modifiers.primary_key.columns.keys() == ["modifier_id"]
    assert mappings.primary_key.columns.keys() == ["product_modifier_group_id"]
    assert ("cafe_id", "product_id", "code") in _constraint_columns(
        variations,
        UniqueConstraint,
    )
    assert ("cafe_id", "name") in _constraint_columns(groups, UniqueConstraint)
    assert ("cafe_id", "group_id", "name") in _constraint_columns(
        modifiers,
        UniqueConstraint,
    )
    assert ("cafe_id", "product_id", "group_id") in _constraint_columns(
        mappings,
        UniqueConstraint,
    )
    assert "price_cents >= 0" in _check_constraint_sql(variations)
    assert "selection_type IN ('single', 'multiple')" in _check_constraint_sql(
        groups
    )
    assert "max_select >= min_select" in _check_constraint_sql(groups)
    assert "price_cents >= 0" in _check_constraint_sql(modifiers)
    assert "fk_product_variations_product_cafe" in {
        constraint.name for constraint in variations.foreign_key_constraints
    }
    assert "fk_modifiers_group_cafe" in {
        constraint.name for constraint in modifiers.foreign_key_constraints
    }
    assert {
        "fk_product_modifier_groups_product_cafe",
        "fk_product_modifier_groups_group_cafe",
    }.issubset(
        {constraint.name for constraint in mappings.foreign_key_constraints}
    )
    assert "ix_product_variations_cafe_id_product_id_active" in {
        index.name for index in variations.indexes
    }
    assert "uq_modifiers_cafe_group_default" in {
        index.name for index in modifiers.indexes
    }


def test_sales_relationships_configure_without_overlap_warnings() -> None:
    assert inspect(Cafe).relationships["menu_products"].mapper.class_ is MenuProduct
    assert inspect(Cafe).relationships["sales"].mapper.class_ is Sale
    assert inspect(MenuProduct).relationships["sale_items"].mapper.class_ is SaleItem
    assert (
        inspect(MenuProduct).relationships["variations"].mapper.class_
        is ProductVariation
    )
    assert inspect(ProductVariation).relationships["product"].mapper.class_ is MenuProduct
    assert inspect(ModifierGroup).relationships["modifiers"].mapper.class_ is Modifier
    assert inspect(Modifier).relationships["group"].mapper.class_ is ModifierGroup
    assert (
        inspect(ProductModifierGroup).relationships["product"].mapper.class_
        is MenuProduct
    )
    assert (
        inspect(ProductModifierGroup).relationships["group"].mapper.class_
        is ModifierGroup
    )
    assert inspect(Sale).relationships["items"].mapper.class_ is SaleItem
    assert inspect(SaleItem).relationships["sale"].mapper.class_ is Sale
    assert inspect(SaleItem).relationships["product"].mapper.class_ is MenuProduct
