from datetime import datetime

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from backend.models.cafe import Cafe
from backend.modules.sales.models import MenuProduct, Sale, SaleItem


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

    def create_sale(self, sale: Sale, items: list[SaleItem]) -> None:
        self._database_session.add(sale)
        self._database_session.add_all(items)
        self._database_session.flush()

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
