from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_INVENTORY_QUANTITY = Decimal("999999999999999.999")


def _validate_quantity(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("counted_quantity must be finite")
    if value < 0:
        raise ValueError("counted_quantity must be nonnegative")
    if -value.as_tuple().exponent > 3:
        raise ValueError("counted_quantity supports at most three decimal places")
    if value > MAX_INVENTORY_QUANTITY:
        raise ValueError("counted_quantity exceeds NUMERIC(18,3)")
    return value


def _validate_observation_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("counted_at must include a timezone")
    if value > datetime.now(timezone.utc):
        raise ValueError("counted_at cannot be in the future")
    return value


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


class StockCountItemCreate(BaseModel):
    ingredient_id: str
    counted_quantity: Decimal

    @field_validator("counted_quantity")
    @classmethod
    def validate_counted_quantity(cls, value: Decimal) -> Decimal:
        return _validate_quantity(value)


class StockCountCreate(BaseModel):
    cafe_id: str = "CAFE001"
    status: Literal["draft", "verified"] = "draft"
    counted_at: datetime | None = None
    counted_by: str | None = None
    is_synthetic: bool = False
    items: list[StockCountItemCreate]

    @field_validator("counted_at")
    @classmethod
    def validate_counted_at(cls, value: datetime | None) -> datetime | None:
        return _validate_observation_timestamp(value)

    @model_validator(mode="after")
    def validate_count(self) -> "StockCountCreate":
        if not self.items:
            raise ValueError("at least one counted item is required")
        ingredient_ids = [item.ingredient_id for item in self.items]
        if len(ingredient_ids) != len(set(ingredient_ids)):
            raise ValueError("duplicate ingredient_id values are not allowed")
        if self.status == "verified" and self.counted_at is None:
            raise ValueError("verified counts require counted_at")
        return self


class StockCountVerify(BaseModel):
    counted_at: datetime | None = None

    @field_validator("counted_at")
    @classmethod
    def validate_counted_at(cls, value: datetime | None) -> datetime | None:
        return _validate_observation_timestamp(value)


class StockCountLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    count_line_id: str
    ingredient_id: str
    counted_quantity: Decimal


class StockCountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    count_id: str
    cafe_id: str
    status: str
    counted_at: datetime | None
    recorded_at: datetime
    counted_by: str | None
    is_synthetic: bool
    lines: list[StockCountLineResponse]


class StockCountSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    count_id: str
    cafe_id: str
    status: str
    counted_at: datetime | None
    recorded_at: datetime
    counted_by: str | None
    is_synthetic: bool
    item_count: int


class InventoryMovementCreate(BaseModel):
    cafe_id: str = "CAFE001"
    ingredient_id: str
    movement_type: Literal["RECEIPT", "WASTE", "ADJUSTMENT"]
    quantity_delta: Decimal
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    occurred_at: datetime
    recorded_by: str | None = None
    reason: str | None = None

    @field_validator("source_type", "source_id")
    @classmethod
    def validate_source_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source identity values must not be blank")
        return value

    @field_validator("quantity_delta")
    @classmethod
    def validate_quantity_delta(cls, value: Decimal) -> Decimal:
        _validate_quantity(value.copy_abs())
        if value == 0:
            raise ValueError("quantity_delta must be nonzero")
        return value

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        validated_value = _validate_observation_timestamp(value)
        assert validated_value is not None
        return validated_value

    @model_validator(mode="after")
    def validate_movement(self) -> "InventoryMovementCreate":
        if self.movement_type == "RECEIPT" and self.quantity_delta <= 0:
            raise ValueError("RECEIPT requires a positive quantity_delta")
        if self.movement_type == "WASTE" and self.quantity_delta >= 0:
            raise ValueError("WASTE requires a negative quantity_delta")
        if self.movement_type == "ADJUSTMENT" and self.quantity_delta == 0:
            raise ValueError("ADJUSTMENT requires a nonzero quantity_delta")
        if self.movement_type in {"WASTE", "ADJUSTMENT"} and not (
            self.reason and self.reason.strip()
        ):
            raise ValueError(f"{self.movement_type} requires a reason")
        return self


class InventoryMovementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
