from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class InventoryItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ingredient_id: str
    cafe_id: str
    name: str
    category: str
    base_unit: str
    reorder_point: Decimal | None
    par_level: Decimal | None
    is_perishable: bool
    active: bool
