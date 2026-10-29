"""Order math done in code, never by the model.

need (recipes x expected mix) + buffer (café policy %) - usable stock = order.
Unknown or inconsistent stock is reported, never treated as zero, and never ordered blind.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from backend.modules.agent.cafe_policies import policies_for
from backend.modules.inventory.service import InventoryService
from backend.modules.sales.schemas import SaleItemV2Create, UsageProjectionRequest
from backend.modules.sales.service import SalesService

QUANTITY = Decimal("0.001")
DEFAULT_BUFFER_PCT = Decimal("10")


class ShortfallInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cafe_id: str = "CAFE001"
    as_of: datetime = Field(description="Stock knowledge cutoff, with timezone offset.")
    target_at: datetime = Field(description="When the extra demand happens, with timezone offset.")
    items: list[SaleItemV2Create] = Field(min_length=1, description="Expected extra product mix.")
    buffer_pct: Decimal | None = Field(
        default=None, ge=0, le=100, description="Safety buffer on top of the need. Omit to use café policy."
    )

    @field_validator("as_of", "target_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must include a timezone offset")
        return value


class ShortfallLine(BaseModel):
    ingredient_id: str
    name: str
    unit: str
    needed: Decimal
    buffer: Decimal
    needed_with_buffer: Decimal
    on_hand: Decimal | None
    stock_status: str
    order_quantity: Decimal | None
    status: Literal["enough", "short", "unknown_stock"]


class OrderLine(BaseModel):
    ingredient_id: str
    quantity: Decimal
    unit: str


class ShortfallResult(BaseModel):
    cafe_id: str
    as_of: datetime
    target_at: datetime
    buffer_pct: Decimal
    lines: list[ShortfallLine]
    order_lines: list[OrderLine]
    unknown_stock: list[str]
    unmapped_variations: list[str]


def _round_up(value: Decimal, unit: str) -> Decimal:
    step = Decimal("1")  # whole mL, g or each
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step if unit else value


def calculate_shortfall(session: Session, params: ShortfallInput) -> ShortfallResult:
    policy_pct = policies_for(params.cafe_id).get("ordering", {}).get("buffer_pct")
    buffer_pct = params.buffer_pct if params.buffer_pct is not None else Decimal(str(policy_pct or DEFAULT_BUFFER_PCT))

    projection = SalesService(session).project_usage(
        UsageProjectionRequest(cafe_id=params.cafe_id, target_at=params.target_at, items=params.items)
    )
    estimates = {
        estimate.ingredient_id: estimate
        for estimate in InventoryService(session).list_stock_estimates(params.cafe_id, None, params.as_of)
    }

    lines: list[ShortfallLine] = []
    for projected in projection.ingredients:
        needed = Decimal(projected.quantity)
        buffer = (needed * buffer_pct / 100).quantize(QUANTITY)
        target = needed + buffer
        estimate = estimates.get(projected.ingredient_id)
        known = estimate is not None and estimate.stock_status == "known"
        on_hand = estimate.estimated_quantity if known else None
        if on_hand is None:
            status, order = "unknown_stock", None
        elif on_hand >= target:
            status, order = "enough", Decimal("0")
        else:
            status, order = "short", _round_up(target - on_hand, projected.unit)
        lines.append(
            ShortfallLine(
                ingredient_id=projected.ingredient_id,
                name=projected.name,
                unit=projected.unit,
                needed=needed,
                buffer=buffer,
                needed_with_buffer=target,
                on_hand=on_hand,
                stock_status=estimate.stock_status if estimate else "unknown",
                order_quantity=order,
                status=status,
            )
        )

    return ShortfallResult(
        cafe_id=params.cafe_id,
        as_of=params.as_of,
        target_at=params.target_at,
        buffer_pct=buffer_pct,
        lines=lines,
        order_lines=[
            OrderLine(ingredient_id=line.ingredient_id, quantity=line.order_quantity, unit=line.unit)
            for line in lines
            if line.status == "short"
        ],
        unknown_stock=[line.ingredient_id for line in lines if line.status == "unknown_stock"],
        unmapped_variations=list(projection.unmapped_variations),
    )
