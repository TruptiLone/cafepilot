from collections.abc import Sequence

from datetime import datetime

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session, contains_eager

from backend.models.cafe import Cafe
from backend.modules.inventory.models import (
    InventoryItem,
    InventoryMovement,
    StockCount,
    StockCountLine,
)


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

    def get_items(self, cafe_id: str, ingredient_ids: set[str]) -> list[InventoryItem]:
        statement = select(InventoryItem).where(
            InventoryItem.cafe_id == cafe_id,
            InventoryItem.ingredient_id.in_(ingredient_ids),
        )
        return list(self._database_session.scalars(statement))

    def create_count(self, count: StockCount, lines: Sequence[StockCountLine]) -> None:
        self._database_session.add(count)
        self._database_session.add_all(lines)
        self._database_session.flush()

    def list_counts(
        self,
        cafe_id: str,
        status: str | None = None,
    ) -> list[tuple[StockCount, int]]:
        statement = (
            select(StockCount, func.count(StockCountLine.count_line_id))
            .outerjoin(StockCountLine, StockCount.count_id == StockCountLine.count_id)
            .where(StockCount.cafe_id == cafe_id)
            .group_by(StockCount.count_id)
            .order_by(StockCount.recorded_at.desc(), StockCount.count_id.desc())
        )
        if status is not None:
            statement = statement.where(StockCount.status == status)
        return [(count, int(item_count)) for count, item_count in self._database_session.execute(statement)]

    def get_count(self, cafe_id: str, count_id: str) -> StockCount | None:
        statement = (
            select(StockCount)
            .outerjoin(StockCountLine, StockCount.count_id == StockCountLine.count_id)
            .options(contains_eager(StockCount.lines))
            .where(
                StockCount.cafe_id == cafe_id,
                StockCount.count_id == count_id,
            )
            .order_by(StockCountLine.ingredient_id)
        )
        return self._database_session.execute(statement).unique().scalar_one_or_none()

    def verify_count(
        self,
        cafe_id: str,
        count_id: str,
        counted_at: datetime | None,
    ) -> bool:
        values: dict[str, object] = {"status": "verified"}
        if counted_at is not None:
            values["counted_at"] = func.coalesce(StockCount.counted_at, counted_at)
        result = self._database_session.execute(
            update(StockCount)
            .where(
                StockCount.cafe_id == cafe_id,
                StockCount.count_id == count_id,
                StockCount.status == "draft",
            )
            .values(**values)
        )
        self._database_session.flush()
        return result.rowcount == 1

    def create_movement(self, movement: InventoryMovement) -> None:
        self._database_session.add(movement)
        self._database_session.flush()

    def get_movement_by_identity(
        self,
        ingredient_id: str,
        source_type: str,
        source_id: str,
    ) -> tuple[InventoryMovement, InventoryItem] | None:
        statement = (
            select(InventoryMovement, InventoryItem)
            .join(InventoryItem, InventoryMovement.ingredient_id == InventoryItem.ingredient_id)
            .where(
                InventoryMovement.ingredient_id == ingredient_id,
                InventoryMovement.source_type == source_type,
                InventoryMovement.source_id == source_id,
            )
        )
        return self._database_session.execute(statement).one_or_none()

    def list_movements(
        self,
        cafe_id: str,
        ingredient_id: str | None = None,
        movement_type: str | None = None,
    ) -> list[tuple[InventoryMovement, InventoryItem]]:
        statement = (
            select(InventoryMovement, InventoryItem)
            .join(InventoryItem, InventoryMovement.ingredient_id == InventoryItem.ingredient_id)
            .where(InventoryMovement.cafe_id == cafe_id)
            .order_by(
                InventoryMovement.occurred_at.desc(),
                InventoryMovement.recorded_at.desc(),
                InventoryMovement.movement_id.desc(),
            )
        )
        if ingredient_id is not None:
            statement = statement.where(InventoryMovement.ingredient_id == ingredient_id)
        if movement_type is not None:
            statement = statement.where(InventoryMovement.movement_type == movement_type)
        return list(self._database_session.execute(statement))

    def list_stock_items(
        self,
        cafe_id: str,
        ingredient_id: str | None = None,
    ) -> list[InventoryItem]:
        statement = select(InventoryItem).where(InventoryItem.cafe_id == cafe_id)
        if ingredient_id is not None:
            statement = statement.where(InventoryItem.ingredient_id == ingredient_id)
        statement = statement.order_by(InventoryItem.name, InventoryItem.ingredient_id)
        return list(self._database_session.scalars(statement))

    def list_verified_count_lines(
        self,
        cafe_id: str,
        as_of: datetime,
        ingredient_id: str | None = None,
    ) -> list[tuple[StockCountLine, StockCount]]:
        statement = (
            select(StockCountLine, StockCount)
            .join(StockCount, StockCountLine.count_id == StockCount.count_id)
            .where(
                StockCount.cafe_id == cafe_id,
                StockCount.status == "verified",
                StockCount.counted_at.is_not(None),
                StockCount.counted_at <= as_of,
                StockCount.recorded_at <= as_of,
            )
            .order_by(
                StockCountLine.ingredient_id,
                StockCount.counted_at.desc(),
                StockCount.recorded_at.desc(),
                StockCount.count_id.desc(),
                StockCountLine.count_line_id.desc(),
            )
        )
        if ingredient_id is not None:
            statement = statement.where(StockCountLine.ingredient_id == ingredient_id)
        return list(self._database_session.execute(statement))

    def list_eligible_stock_movements(
        self,
        cafe_id: str,
        as_of: datetime,
        ingredient_id: str | None = None,
    ) -> list[InventoryMovement]:
        statement = select(InventoryMovement).where(
            InventoryMovement.cafe_id == cafe_id,
            InventoryMovement.occurred_at <= as_of,
            InventoryMovement.recorded_at <= as_of,
        )
        if ingredient_id is not None:
            statement = statement.where(InventoryMovement.ingredient_id == ingredient_id)
        return list(self._database_session.scalars(statement))
