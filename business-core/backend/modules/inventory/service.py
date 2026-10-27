from sqlalchemy.orm import Session

from backend.modules.inventory.models import InventoryItem
from backend.modules.inventory.repository import InventoryRepository

DEFAULT_CAFE_ID = "CAFE001"


class CafeNotFoundError(LookupError):
    pass


class InventoryItemNotFoundError(LookupError):
    pass


class InventoryService:
    def __init__(self, database_session: Session) -> None:
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
