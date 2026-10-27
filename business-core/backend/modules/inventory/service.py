from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.modules.inventory.models import (
    InventoryItem,
    InventoryMovement,
    StockCount,
    StockCountLine,
)
from backend.modules.inventory.repository import InventoryRepository
from backend.modules.inventory.schemas import (
    InventoryMovementCreate,
    StockCountCreate,
    StockCountVerify,
)

DEFAULT_CAFE_ID = "CAFE001"


class CafeNotFoundError(LookupError):
    pass


class InventoryItemNotFoundError(LookupError):
    pass


class StockCountNotFoundError(LookupError):
    pass


class StockCountConflictError(RuntimeError):
    pass


class StockCountValidationError(ValueError):
    pass


class InventoryMovementConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class StockCountSummary:
    count_id: str
    cafe_id: str
    status: str
    counted_at: datetime | None
    recorded_at: datetime
    counted_by: str | None
    is_synthetic: bool
    item_count: int


@dataclass(frozen=True)
class InventoryMovementResult:
    movement_id: str
    cafe_id: str
    ingredient_id: str
    base_unit: str
    movement_type: str
    quantity_delta: Decimal
    is_estimated: bool
    source_type: str
    source_id: str
    occurred_at: datetime
    recorded_at: datetime
    recorded_by: str | None
    reason: str | None


class InventoryService:
    def __init__(self, database_session: Session) -> None:
        self._database_session = database_session
        self._repository = InventoryRepository(database_session)

    def list_items(
        self,
        cafe_id: str = DEFAULT_CAFE_ID,
        category: str | None = None,
        include_inactive: bool = False,
    ) -> list[InventoryItem]:
        self._require_cafe(cafe_id)
        return self._repository.list_items(cafe_id, category, include_inactive)

    def get_item(
        self,
        ingredient_id: str,
        cafe_id: str = DEFAULT_CAFE_ID,
    ) -> InventoryItem:
        self._require_cafe(cafe_id)
        item = self._repository.get_item(cafe_id, ingredient_id)
        if item is None:
            raise InventoryItemNotFoundError(ingredient_id)
        return item

    def _require_cafe(self, cafe_id: str) -> None:
        if self._repository.get_cafe(cafe_id) is None:
            raise CafeNotFoundError(cafe_id)

    def create_count(self, request: StockCountCreate) -> StockCount:
        count_id = f"count_{uuid4().hex}"
        with self._transaction():
            self._require_cafe(request.cafe_id)
            ingredient_ids = {item.ingredient_id for item in request.items}
            found_items = self._repository.get_items(request.cafe_id, ingredient_ids)
            found_ids = {item.ingredient_id for item in found_items}
            missing_ids = ingredient_ids - found_ids
            if missing_ids:
                raise InventoryItemNotFoundError(next(iter(missing_ids)))

            count = StockCount(
                count_id=count_id,
                cafe_id=request.cafe_id,
                counted_at=request.counted_at,
                recorded_at=datetime.now(timezone.utc),
                counted_by=request.counted_by,
                status=request.status,
                is_synthetic=request.is_synthetic,
            )
            lines = [
                StockCountLine(
                    count_line_id=f"count_line_{uuid4().hex}",
                    count_id=count_id,
                    cafe_id=request.cafe_id,
                    ingredient_id=item.ingredient_id,
                    counted_quantity=item.counted_quantity,
                )
                for item in request.items
            ]
            self._repository.create_count(count, lines)
        return self.get_count(count_id, request.cafe_id)

    def list_counts(
        self,
        cafe_id: str = DEFAULT_CAFE_ID,
        status: str | None = None,
    ) -> list[StockCountSummary]:
        self._require_cafe(cafe_id)
        return [
            StockCountSummary(
                count_id=count.count_id,
                cafe_id=count.cafe_id,
                status=count.status,
                counted_at=count.counted_at,
                recorded_at=count.recorded_at,
                counted_by=count.counted_by,
                is_synthetic=count.is_synthetic,
                item_count=item_count,
            )
            for count, item_count in self._repository.list_counts(cafe_id, status)
        ]

    def get_count(self, count_id: str, cafe_id: str = DEFAULT_CAFE_ID) -> StockCount:
        self._require_cafe(cafe_id)
        count = self._repository.get_count(cafe_id, count_id)
        if count is None:
            raise StockCountNotFoundError(count_id)
        return count

    def verify_count(
        self,
        count_id: str,
        request: StockCountVerify,
        cafe_id: str = DEFAULT_CAFE_ID,
    ) -> StockCount:
        with self._transaction():
            count = self.get_count(count_id, cafe_id)
            if count.status != "draft":
                raise StockCountConflictError("only draft counts can be verified")
            if not count.lines:
                raise StockCountValidationError("count must include at least one ingredient")
            if count.counted_at is None and request.counted_at is None:
                raise StockCountValidationError("verification requires counted_at")
            if count.counted_at is not None and request.counted_at not in (None, count.counted_at):
                raise StockCountValidationError("counted_at cannot be changed during verification")

            if not self._repository.verify_count(cafe_id, count_id, request.counted_at):
                raise StockCountConflictError("only draft counts can be verified")
        return self.get_count(count_id, cafe_id)

    def _transaction(self) -> AbstractContextManager:
        if self._database_session.in_transaction():
            return self._database_session.begin_nested()
        return self._database_session.begin()

    def create_movement(
        self,
        request: InventoryMovementCreate,
    ) -> tuple[InventoryMovementResult, bool]:
        with self._transaction():
            self._require_cafe(request.cafe_id)
            item = self._repository.get_item(request.cafe_id, request.ingredient_id)
            if item is None:
                raise InventoryItemNotFoundError(request.ingredient_id)

            existing = self._repository.get_movement_by_identity(
                request.ingredient_id,
                request.source_type,
                request.source_id,
            )
            if existing is not None:
                return self._resolve_movement_identity(existing, request), False

            movement = InventoryMovement(
                movement_id=f"movement_{uuid4().hex}",
                cafe_id=request.cafe_id,
                ingredient_id=request.ingredient_id,
                movement_type=request.movement_type,
                quantity_delta=request.quantity_delta,
                is_estimated=False,
                source_type=request.source_type,
                source_id=request.source_id,
                occurred_at=request.occurred_at,
                recorded_at=datetime.now(timezone.utc),
                recorded_by=request.recorded_by,
                reason=request.reason,
            )
            try:
                with self._database_session.begin_nested():
                    self._repository.create_movement(movement)
            except IntegrityError:
                existing = self._repository.get_movement_by_identity(
                    request.ingredient_id,
                    request.source_type,
                    request.source_id,
                )
                if existing is None:
                    raise
                return self._resolve_movement_identity(existing, request), False

            return self._movement_result(movement, item), True

    def list_movements(
        self,
        cafe_id: str = DEFAULT_CAFE_ID,
        ingredient_id: str | None = None,
        movement_type: str | None = None,
    ) -> list[InventoryMovementResult]:
        self._require_cafe(cafe_id)
        return [
            self._movement_result(movement, item)
            for movement, item in self._repository.list_movements(
                cafe_id,
                ingredient_id,
                movement_type,
            )
        ]

    def _resolve_movement_identity(
        self,
        existing: tuple[InventoryMovement, InventoryItem],
        request: InventoryMovementCreate,
    ) -> InventoryMovementResult:
        movement, item = existing
        if (
            movement.cafe_id != request.cafe_id
            or movement.movement_type != request.movement_type
            or movement.quantity_delta != request.quantity_delta
            or movement.occurred_at != request.occurred_at
            or movement.recorded_by != request.recorded_by
            or movement.reason != request.reason
        ):
            raise InventoryMovementConflictError(
                "source event identity conflicts with an existing movement"
            )
        return self._movement_result(movement, item)

    @staticmethod
    def _movement_result(
        movement: InventoryMovement,
        item: InventoryItem,
    ) -> InventoryMovementResult:
        return InventoryMovementResult(
            movement_id=movement.movement_id,
            cafe_id=movement.cafe_id,
            ingredient_id=movement.ingredient_id,
            base_unit=item.base_unit,
            movement_type=movement.movement_type,
            quantity_delta=movement.quantity_delta,
            is_estimated=movement.is_estimated,
            source_type=movement.source_type,
            source_id=movement.source_id,
            occurred_at=movement.occurred_at,
            recorded_at=movement.recorded_at,
            recorded_by=movement.recorded_by,
            reason=movement.reason,
        )
