from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from backend.models.cafe import Cafe
from backend.modules.inventory.models import InventoryItem


class InventoryRepository:
    def __init__(self, database_session: Session) -> None:
        self._database_session = database_session

    def get_cafe(self, cafe_id: str) -> Cafe | None:
        return self._database_session.get(Cafe, cafe_id)

    def list_items(
        self,
        cafe_id: str,
        category: str | None = None,
        include_inactive: bool = False,
    ) -> list[InventoryItem]:
        statement: Select[tuple[InventoryItem]] = select(InventoryItem).where(
            InventoryItem.cafe_id == cafe_id
        )
        if category is not None:
            statement = statement.where(InventoryItem.category == category)
        if not include_inactive:
            statement = statement.where(InventoryItem.active.is_(True))

        statement = statement.order_by(InventoryItem.name, InventoryItem.ingredient_id)
        return list(self._database_session.scalars(statement))

    def get_item(self, cafe_id: str, ingredient_id: str) -> InventoryItem | None:
        statement = select(InventoryItem).where(
            InventoryItem.cafe_id == cafe_id,
            InventoryItem.ingredient_id == ingredient_id,
        )
        return self._database_session.scalar(statement)
