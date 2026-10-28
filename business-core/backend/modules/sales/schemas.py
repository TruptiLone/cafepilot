from datetime import datetime, timezone

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


class SaleItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1)
    quantity: int = Field(gt=0, le=2_147_483_647)


class SaleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cafe_id: str = "CAFE001"
    source_id: str = Field(min_length=1)
    sold_at: datetime
    items: list[SaleItemCreate]

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source_id must not be blank")
        return value

    @field_validator("sold_at")
    @classmethod
    def validate_sold_at(cls, value: datetime) -> datetime:
        return validate_aware_timestamp(value)

    @model_validator(mode="after")
    def validate_items(self) -> "SaleCreate":
        if not self.items:
            raise ValueError("at least one sale item is required")
        product_ids = [item.product_id for item in self.items]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("duplicate product_id values are not allowed")
        return self


class SaleItemResponse(BaseModel):
    product_id: str
    product_name: str
    quantity: int
    unit_price_cents: int
    line_total_cents: int


class SaleResponse(BaseModel):
    sale_id: str
    cafe_id: str
    source_id: str
    sold_at: datetime
    recorded_at: datetime
    status: str
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
