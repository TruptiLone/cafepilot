from sqlalchemy import delete
from sqlalchemy.orm import Session

from backend.models.cafe import Cafe
from backend.modules.inventory.models import InventoryItem, InventoryMovement
from backend.modules.sales.models import (
    MenuProduct,
    Modifier,
    ModifierGroup,
    ModifierRecipeEffect,
    ProductModifierGroup,
    ProductVariation,
    RecipeLine,
    Sale,
    SaleItem,
    SaleItemModifier,
)
from scripts.seed_inventory import load_inventory_catalog
from scripts.seed_sales import load_sales_catalog, seed_sales
from scripts.seed_sales_catalog import catalog_for_cafe, catalog_identifier, seed_sales_catalog
from scripts.seed_sales_recipes import seed_sales_recipes


def seed_sales_v2_test_catalog(
    session: Session,
    cafe_id: str,
    *,
    tax_rate_bps: int = 875,
    include_recipes: bool = True,
) -> None:
    session.add(
        Cafe(
            cafe_id=cafe_id,
            name="Sales V2 Test Cafe",
            timezone="America/Los_Angeles",
            tax_rate_bps=tax_rate_bps,
        )
    )
    for item in load_inventory_catalog().items:
        session.add(
            InventoryItem(
                ingredient_id=catalog_identifier(cafe_id, item.ingredient_id),
                cafe_id=cafe_id,
                name=item.name,
                category=item.category,
                base_unit=item.base_unit,
                reorder_point=None,
                par_level=None,
            )
        )
    session.flush()
    catalog = catalog_for_cafe(load_sales_catalog(), cafe_id)
    seed_sales(session, catalog)
    seed_sales_catalog(session, catalog)
    if include_recipes:
        seed_sales_recipes(session, cafe_id)


def delete_sales_v2_test_catalog(session: Session, cafe_id: str) -> None:
    session.execute(delete(InventoryMovement).where(InventoryMovement.cafe_id == cafe_id))
    session.execute(delete(SaleItemModifier).where(SaleItemModifier.cafe_id == cafe_id))
    session.execute(delete(SaleItem).where(SaleItem.cafe_id == cafe_id))
    session.execute(delete(Sale).where(Sale.cafe_id == cafe_id))
    session.execute(delete(ModifierRecipeEffect).where(ModifierRecipeEffect.cafe_id == cafe_id))
    session.execute(delete(RecipeLine).where(RecipeLine.cafe_id == cafe_id))
    session.execute(delete(ProductModifierGroup).where(ProductModifierGroup.cafe_id == cafe_id))
    session.execute(delete(Modifier).where(Modifier.cafe_id == cafe_id))
    session.execute(delete(ModifierGroup).where(ModifierGroup.cafe_id == cafe_id))
    session.execute(delete(ProductVariation).where(ProductVariation.cafe_id == cafe_id))
    session.execute(delete(MenuProduct).where(MenuProduct.cafe_id == cafe_id))
    session.execute(delete(InventoryItem).where(InventoryItem.cafe_id == cafe_id))
    session.execute(delete(Cafe).where(Cafe.cafe_id == cafe_id))
