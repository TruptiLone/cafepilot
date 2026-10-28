from datetime import datetime

from sqlalchemy import and_, func, select
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


class SalesRepository:
    def __init__(self, database_session: Session) -> None:
        self._database_session = database_session

    def get_cafe(self, cafe_id: str) -> Cafe | None:
        return self._database_session.get(Cafe, cafe_id)

    def list_products(self, cafe_id: str, include_inactive: bool = False) -> list[MenuProduct]:
        statement = select(MenuProduct).where(MenuProduct.cafe_id == cafe_id)
        if not include_inactive:
            statement = statement.where(MenuProduct.active.is_(True))
        statement = statement.order_by(MenuProduct.category, MenuProduct.name, MenuProduct.product_id)
        return list(self._database_session.scalars(statement))

    def get_active_products(self, cafe_id: str, product_ids: set[str]) -> list[MenuProduct]:
        statement = select(MenuProduct).where(
            MenuProduct.cafe_id == cafe_id,
            MenuProduct.product_id.in_(product_ids),
            MenuProduct.active.is_(True),
        )
        return list(self._database_session.scalars(statement))

    def get_active_regular_catalog(
        self,
        cafe_id: str,
        product_ids: set[str],
    ) -> list[tuple[MenuProduct, ProductVariation]]:
        statement = (
            select(MenuProduct, ProductVariation)
            .join(
                ProductVariation,
                and_(
                    MenuProduct.product_id == ProductVariation.product_id,
                    MenuProduct.cafe_id == ProductVariation.cafe_id,
                ),
            )
            .where(
                MenuProduct.cafe_id == cafe_id,
                MenuProduct.product_id.in_(product_ids),
                MenuProduct.active.is_(True),
                ProductVariation.code == "REG",
                ProductVariation.active.is_(True),
            )
        )
        return list(self._database_session.execute(statement))

    def get_active_variation_catalog(
        self,
        cafe_id: str,
        variation_ids: set[str],
    ) -> list[tuple[ProductVariation, MenuProduct]]:
        statement = (
            select(ProductVariation, MenuProduct)
            .join(
                MenuProduct,
                and_(
                    ProductVariation.product_id == MenuProduct.product_id,
                    ProductVariation.cafe_id == MenuProduct.cafe_id,
                ),
            )
            .where(
                ProductVariation.cafe_id == cafe_id,
                ProductVariation.variation_id.in_(variation_ids),
                ProductVariation.active.is_(True),
                MenuProduct.active.is_(True),
            )
        )
        return list(self._database_session.execute(statement))

    def get_active_modifier_catalog(
        self,
        cafe_id: str,
        product_ids: set[str],
    ) -> tuple[
        list[tuple[ProductModifierGroup, ModifierGroup]],
        list[Modifier],
    ]:
        mappings = list(
            self._database_session.execute(
                select(ProductModifierGroup, ModifierGroup)
                .join(
                    ModifierGroup,
                    and_(
                        ProductModifierGroup.group_id == ModifierGroup.group_id,
                        ProductModifierGroup.cafe_id == ModifierGroup.cafe_id,
                    ),
                )
                .where(
                    ProductModifierGroup.cafe_id == cafe_id,
                    ProductModifierGroup.product_id.in_(product_ids),
                    ProductModifierGroup.active.is_(True),
                    ModifierGroup.active.is_(True),
                )
            )
        )
        group_ids = {group.group_id for _, group in mappings}
        if not group_ids:
            return mappings, []
        modifiers = list(
            self._database_session.scalars(
                select(Modifier).where(
                    Modifier.cafe_id == cafe_id,
                    Modifier.group_id.in_(group_ids),
                    Modifier.active.is_(True),
                )
            )
        )
        return mappings, modifiers

    def get_modifiers_by_ids(
        self,
        cafe_id: str,
        modifier_ids: set[str],
    ) -> list[Modifier]:
        if not modifier_ids:
            return []
        return list(
            self._database_session.scalars(
                select(Modifier).where(
                    Modifier.cafe_id == cafe_id,
                    Modifier.modifier_id.in_(modifier_ids),
                )
            )
        )

    def get_recipe_lines(
        self,
        cafe_id: str,
        variation_id: str,
        sold_at: datetime,
    ) -> list[tuple[RecipeLine, InventoryItem]]:
        effective_from = self._database_session.scalar(
            select(func.max(RecipeLine.effective_from)).where(
                RecipeLine.cafe_id == cafe_id,
                RecipeLine.variation_id == variation_id,
                RecipeLine.effective_from <= sold_at,
            )
        )
        if effective_from is None:
            return []
        statement = (
            select(RecipeLine, InventoryItem)
            .join(
                InventoryItem,
                and_(
                    RecipeLine.ingredient_id == InventoryItem.ingredient_id,
                    RecipeLine.cafe_id == InventoryItem.cafe_id,
                ),
            )
            .where(
                RecipeLine.cafe_id == cafe_id,
                RecipeLine.variation_id == variation_id,
                RecipeLine.effective_from == effective_from,
            )
            .order_by(RecipeLine.ingredient_id)
        )
        return list(self._database_session.execute(statement))

    def get_modifier_effects(
        self,
        cafe_id: str,
        modifier_ids: set[str],
    ) -> list[ModifierRecipeEffect]:
        if not modifier_ids:
            return []
        return list(
            self._database_session.scalars(
                select(ModifierRecipeEffect)
                .where(
                    ModifierRecipeEffect.cafe_id == cafe_id,
                    ModifierRecipeEffect.modifier_id.in_(modifier_ids),
                )
                .order_by(
                    ModifierRecipeEffect.modifier_id,
                    ModifierRecipeEffect.modifier_recipe_effect_id,
                )
            )
        )

    def get_inventory_items(
        self,
        cafe_id: str,
        ingredient_ids: set[str],
    ) -> list[InventoryItem]:
        if not ingredient_ids:
            return []
        return list(
            self._database_session.scalars(
                select(InventoryItem).where(
                    InventoryItem.cafe_id == cafe_id,
                    InventoryItem.ingredient_id.in_(ingredient_ids),
                )
            )
        )

    def create_sale(
        self,
        sale: Sale,
        items: list[SaleItem],
        modifiers: list[SaleItemModifier],
        movements: list[InventoryMovement],
    ) -> None:
        self._database_session.add(sale)
        self._database_session.add_all(items)
        self._database_session.flush()
        self._database_session.add_all(modifiers)
        self._database_session.add_all(movements)
        self._database_session.flush()

    def get_sale_modifiers(
        self,
        sale_item_ids: set[str],
    ) -> list[SaleItemModifier]:
        if not sale_item_ids:
            return []
        return list(
            self._database_session.scalars(
                select(SaleItemModifier)
                .where(SaleItemModifier.sale_item_id.in_(sale_item_ids))
                .order_by(
                    SaleItemModifier.sale_item_id,
                    SaleItemModifier.modifier_id,
                )
            )
        )

    def get_sale_usage_movements(
        self,
        sale_item_ids: set[str],
    ) -> list[InventoryMovement]:
        if not sale_item_ids:
            return []
        return list(
            self._database_session.scalars(
                select(InventoryMovement).where(
                    InventoryMovement.source_type == "sale_item",
                    InventoryMovement.source_id.in_(sale_item_ids),
                    InventoryMovement.movement_type == "SALE_USAGE",
                )
            )
        )

    def get_sale_rows(self, cafe_id: str, sale_id: str) -> list[tuple[Sale, SaleItem, MenuProduct]]:
        statement = (
            select(Sale, SaleItem, MenuProduct)
            .join(
                SaleItem,
                and_(
                    Sale.sale_id == SaleItem.sale_id,
                    Sale.cafe_id == SaleItem.cafe_id,
                ),
            )
            .join(
                MenuProduct,
                and_(
                    SaleItem.product_id == MenuProduct.product_id,
                    SaleItem.cafe_id == MenuProduct.cafe_id,
                ),
            )
            .where(Sale.cafe_id == cafe_id, Sale.sale_id == sale_id)
            .order_by(MenuProduct.name, SaleItem.product_id, SaleItem.sale_item_id)
        )
        return list(self._database_session.execute(statement))

    def get_sale_by_source(self, cafe_id: str, source_id: str) -> list[tuple[Sale, SaleItem, MenuProduct]]:
        statement = (
            select(Sale, SaleItem, MenuProduct)
            .join(
                SaleItem,
                and_(
                    Sale.sale_id == SaleItem.sale_id,
                    Sale.cafe_id == SaleItem.cafe_id,
                ),
            )
            .join(
                MenuProduct,
                and_(
                    SaleItem.product_id == MenuProduct.product_id,
                    SaleItem.cafe_id == MenuProduct.cafe_id,
                ),
            )
            .where(Sale.cafe_id == cafe_id, Sale.source_id == source_id)
            .order_by(MenuProduct.name, SaleItem.product_id, SaleItem.sale_item_id)
        )
        return list(self._database_session.execute(statement))

    def get_sale_by_external_identity(
        self,
        cafe_id: str,
        source: str,
        external_id: str,
    ) -> list[tuple[Sale, SaleItem, MenuProduct]]:
        sale_id = self._database_session.scalar(
            select(Sale.sale_id).where(
                Sale.cafe_id == cafe_id,
                Sale.source == source,
                Sale.external_id == external_id,
            )
        )
        if sale_id is None:
            return []
        return self.get_sale_rows(cafe_id, sale_id)

    def list_sales(
        self,
        cafe_id: str,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> list[tuple[Sale, int, int]]:
        statement = (
            select(
                Sale,
                func.count(SaleItem.sale_item_id),
                func.coalesce(func.sum(SaleItem.quantity), 0),
            )
            .join(
                SaleItem,
                and_(
                    Sale.sale_id == SaleItem.sale_id,
                    Sale.cafe_id == SaleItem.cafe_id,
                ),
            )
            .where(Sale.cafe_id == cafe_id, Sale.status == "completed")
            .group_by(Sale.sale_id)
            .order_by(Sale.sold_at.desc(), Sale.sale_id.desc())
        )
        if start_at is not None:
            statement = statement.where(Sale.sold_at >= start_at)
        if end_at is not None:
            statement = statement.where(Sale.sold_at < end_at)
        return [
            (sale, int(item_count), int(total_units))
            for sale, item_count, total_units in self._database_session.execute(statement)
        ]

    def report_rows(
        self,
        cafe_id: str,
        start_at: datetime | None,
        end_at: datetime | None,
    ) -> list[tuple[Sale, SaleItem, MenuProduct]]:
        statement = (
            select(Sale, SaleItem, MenuProduct)
            .join(
                SaleItem,
                and_(
                    Sale.sale_id == SaleItem.sale_id,
                    Sale.cafe_id == SaleItem.cafe_id,
                ),
            )
            .join(
                MenuProduct,
                and_(
                    SaleItem.product_id == MenuProduct.product_id,
                    SaleItem.cafe_id == MenuProduct.cafe_id,
                ),
            )
            .where(Sale.cafe_id == cafe_id, Sale.status == "completed")
            .order_by(Sale.sold_at, Sale.sale_id, SaleItem.sale_item_id)
        )
        if start_at is not None:
            statement = statement.where(Sale.sold_at >= start_at)
        if end_at is not None:
            statement = statement.where(Sale.sold_at < end_at)
        return list(self._database_session.execute(statement))
