from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def validate_aware_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    if value > datetime.now(timezone.utc):
        raise ValueError("timestamp cannot be in the future")
    return value


class MenuProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: str
    cafe_id: str
    name: str
    category: str
    price_cents: int
    active: bool


class LegacySaleItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1)
    quantity: int = Field(gt=0, le=2_147_483_647)


class SaleModifierCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modifier_id: str = Field(min_length=1)
    quantity: int = Field(ge=1, le=1)


class SaleItemV2Create(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variation_id: str = Field(min_length=1)
    product_id: str | None = Field(default=None, min_length=1)
    quantity: int = Field(gt=0, le=2_147_483_647)
    modifiers: list[SaleModifierCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_modifiers(self) -> "SaleItemV2Create":
        modifier_ids = [modifier.modifier_id for modifier in self.modifiers]
        if len(modifier_ids) != len(set(modifier_ids)):
            raise ValueError("duplicate modifier_id values are not allowed")
        return self


class SaleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cafe_id: str = "CAFE001"
    source_id: str = Field(min_length=1)
    source: Literal["pos", "generated", "csv_import"] | None = None
    external_id: str | None = None
    sold_at: datetime
    discount_cents: int | None = Field(default=None, ge=0, le=2_147_483_647)
    items: list[LegacySaleItemCreate | SaleItemV2Create]

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_id must not be blank")
        return value

    @field_validator("external_id")
    @classmethod
    def validate_external_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("external_id must not be blank")
        return value

    @field_validator("sold_at")
    @classmethod
    def validate_sold_at(cls, value: datetime) -> datetime:
        return validate_aware_timestamp(value)

    @model_validator(mode="after")
    def validate_items(self) -> "SaleCreate":
        if not self.items:
            raise ValueError("at least one sale item is required")
        v2_flags = [isinstance(item, SaleItemV2Create) for item in self.items]
        if any(v2_flags) and not all(v2_flags):
            raise ValueError("legacy and V2 sale items cannot be mixed")
        if not any(v2_flags):
            if (
                self.source is not None
                or self.external_id is not None
                or self.discount_cents is not None
            ):
                raise ValueError("legacy requests cannot include V2 sale fields")
            product_ids = [item.product_id for item in self.items]
            if len(product_ids) != len(set(product_ids)):
                raise ValueError("duplicate product_id values are not allowed")
        else:
            if self.source is None:
                raise ValueError("V2 requests require source")
            if self.source in {"generated", "csv_import"} and self.external_id is None:
                raise ValueError("generated and csv_import requests require external_id")
        return self

    @property
    def is_v2(self) -> bool:
        return bool(self.items) and isinstance(self.items[0], SaleItemV2Create)


class SaleItemModifierResponse(BaseModel):
    modifier_id: str
    modifier_name: str
    price_cents: int
    quantity: int


class SaleItemResponse(BaseModel):
    sale_item_id: str
    product_id: str
    product_name: str
    variation_id: str
    variation_name: str
    quantity: int
    unit_price_cents: int
    modifier_unit_cents: int
    line_total_cents: int
    modifiers: list[SaleItemModifierResponse]


class SaleResponse(BaseModel):
    sale_id: str
    cafe_id: str
    source_id: str
    source: str
    external_id: str | None
    sold_at: datetime
    recorded_at: datetime
    status: str
    subtotal_cents: int
    discount_cents: int
    tax_cents: int
    total_cents: int
    items: list[SaleItemResponse]


class SaleSummaryResponse(BaseModel):
    sale_id: str
    cafe_id: str
    source_id: str
    sold_at: datetime
    recorded_at: datetime
    status: str
    total_cents: int
    item_count: int
    total_units: int


class TopProductResponse(BaseModel):
    product_id: str
    name: str
    quantity: int
    revenue_cents: int


class HourlySalesResponse(BaseModel):
    hour: int
    sales_count: int
    units_sold: int
    revenue_cents: int


class SalesReportResponse(BaseModel):
    cafe_id: str
    start_at: datetime | None
    end_at: datetime | None
    total_revenue_cents: int
    sales_count: int
    total_units_sold: int
    average_sale_value_cents: int
    top_products: list[TopProductResponse]
    hourly_sales: list[HourlySalesResponse]


class IngredientUsageItemResponse(BaseModel):
    ingredient_id: str
    quantity: Decimal
    unit: str


class IngredientUsageResponse(BaseModel):
    sale_id: str
    source: str
    usage_status: Literal["computed", "partial", "unmapped"]
    movement_posted: bool
    is_estimated: bool
    ingredients: list[IngredientUsageItemResponse]
    missing_recipe_variations: list[str]
