"""Order math done in code, never by the model.

Without normal use:   order = extra need + buffer - on hand          (then rounded up to whole packs)
With normal use:      available = on hand + regular deliveries - normal use until cover_until
                      order     = extra need + buffer - available

Extra need comes from recipes x the expected extra product mix (usually the scenario items of
estimate_event_demand). Normal use comes from sales history (same weekday, hours, no events).
Unknown or inconsistent stock is reported, never treated as zero, and never ordered blind.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import ROUND_CEILING, Decimal
from zoneinfo import ZoneInfo
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from backend.models.cafe import Cafe
from backend.modules.agent.cafe_policies import opening_hours, policies_for
from backend.modules.agent.demand import normal_items_between
from backend.modules.inventory.service import InventoryService
from backend.modules.sales.schemas import SaleItemV2Create, UsageProjectionRequest
from backend.modules.sales.service import SalesService

QUANTITY = Decimal("0.001")
DEFAULT_BUFFER_PCT = Decimal("10")


class IncompleteRecipeError(ValueError):
    """The product mix includes items without a recipe, so needs cannot be calculated."""


class ShortfallInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cafe_id: str = "CAFE001"
    as_of: datetime = Field(description="Stock knowledge cutoff, with timezone offset.")
    target_at: datetime = Field(description="When the extra demand happens, with timezone offset.")
    items: list[SaleItemV2Create] = Field(
        default_factory=list,
        description='Expected extra product mix, e.g. "$<estimate_event_demand step>.scenarios.typical.items".',
    )
    include_normal_use: bool = Field(
        default=False,
        description="Subtract normal sales (from history) and add regular deliveries between as_of and cover_until.",
    )
    cover_until: datetime | None = Field(
        default=None, description="End of the period stock must last. Omit for closing time on the target day."
    )
    buffer_pct: Decimal | None = Field(
        default=None, ge=0, le=100, description="Safety buffer on top of the need. Omit to use café policy."
    )

    @field_validator("as_of", "target_at", "cover_until")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
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
    deliveries: Decimal = Field(default=Decimal("0"), description="Regular deliveries expected before cover_until.")
    normal_use: Decimal = Field(default=Decimal("0"), description="Normal sales use expected before cover_until.")
    available: Decimal | None = Field(default=None, description="on_hand + deliveries - normal_use.")
    shortfall: Decimal | None = Field(default=None, description="needed_with_buffer - available, before pack rounding.")
    pack_size: Decimal | None = Field(default=None, description="Orders are rounded up to whole packs of this size.")
    pack_name: str | None = None
    packs: int | None = None
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
    include_normal_use: bool = False
    cover_until: datetime | None = None
    normal_items: int = Field(default=0, description="Normal-day items counted in normal_use.")
    notes: list[str] = Field(default_factory=list)
    lines: list[ShortfallLine]
    order_lines: list[OrderLine]
    unknown_stock: list[str]
    unmapped_variations: list[str]


def _round_up(value: Decimal, unit: str) -> Decimal:
    step = Decimal("1")  # whole mL, g or each
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step if unit else value


def _project(session: Session, cafe_id: str, at: datetime, items: list[SaleItemV2Create]) -> dict[str, tuple[str, str, Decimal]]:
    """ingredient_id -> (name, unit, quantity) for a product mix; fails closed on missing recipes."""
    if not items:
        return {}
    projection = SalesService(session).project_usage(UsageProjectionRequest(cafe_id=cafe_id, target_at=at, items=items))
    if projection.projection_status != "computed":
        # Fail closed: an incomplete recipe understates every total, so no order is safe.
        missing = ", ".join(projection.unmapped_variations) or "some items"
        raise IncompleteRecipeError(
            f"Can't work out the order: no recipe for {missing}. Add the recipe or remove it from the mix."
        )
    return {i.ingredient_id: (i.name, i.unit, Decimal(i.quantity)) for i in projection.ingredients}


def _cover_until(session: Session, params: ShortfallInput) -> datetime:
    if params.cover_until is not None:
        return params.cover_until
    cafe = session.get(Cafe, params.cafe_id)
    tz = ZoneInfo(cafe.timezone if cafe else "UTC")
    local = params.target_at.astimezone(tz)
    close = opening_hours(params.cafe_id).get(local.weekday(), (7, 19))[1]
    return datetime.combine(local.date(), time(close), tz)


def _deliveries(session: Session, cafe_id: str, start: datetime, end: datetime) -> dict[str, Decimal]:
    cafe = session.get(Cafe, cafe_id)
    tz = ZoneInfo(cafe.timezone if cafe else "UTC")
    totals: dict[str, Decimal] = {}
    for rule in policies_for(cafe_id).get("standing_deliveries", []):
        hour, minute = (int(x) for x in rule.get("at", "06:00").split(":"))
        day = start.astimezone(tz).date()
        while day <= end.astimezone(tz).date():
            arrives = datetime.combine(day, time(hour, minute), tz)
            if start < arrives <= end and day.weekday() in rule["weekdays"]:
                totals[rule["ingredient_id"]] = totals.get(rule["ingredient_id"], Decimal("0")) + Decimal(rule["quantity"])
            day += timedelta(days=1)
    return totals


def calculate_shortfall(session: Session, params: ShortfallInput) -> ShortfallResult:
    policy_pct = policies_for(params.cafe_id).get("ordering", {}).get("buffer_pct")
    buffer_pct = params.buffer_pct if params.buffer_pct is not None else Decimal(str(policy_pct or DEFAULT_BUFFER_PCT))

    extra = _project(session, params.cafe_id, params.target_at, params.items)
    normal: dict[str, tuple[str, str, Decimal]] = {}
    deliveries: dict[str, Decimal] = {}
    notes: list[str] = []
    cover_until = None
    normal_count = 0
    if params.include_normal_use:
        cover_until = _cover_until(session, params)
        normal_items, notes = normal_items_between(
            session, params.cafe_id, params.as_of, cover_until, opening_hours(params.cafe_id)
        )
        normal_count = sum(item.quantity for item in normal_items)
        normal = _project(session, params.cafe_id, params.target_at, normal_items)
        deliveries = _deliveries(session, params.cafe_id, params.as_of, cover_until)

    estimates = {
        estimate.ingredient_id: estimate
        for estimate in InventoryService(session).list_stock_estimates(params.cafe_id, None, params.as_of)
    }

    packs_policy = policies_for(params.cafe_id).get("ordering", {}).get("pack_sizes", {})
    lines: list[ShortfallLine] = []
    # Every ingredient the event needs, plus any that normal trading alone would run out of.
    ingredient_ids = list(extra)
    for ingredient_id, (_, _, use) in normal.items():
        if ingredient_id in extra:
            continue
        estimate = estimates.get(ingredient_id)
        if estimate is not None and estimate.stock_status == "known":
            if estimate.estimated_quantity + deliveries.get(ingredient_id, Decimal("0")) < use:
                ingredient_ids.append(ingredient_id)
    for ingredient_id in ingredient_ids:
        name, unit, needed = extra.get(ingredient_id) or (normal[ingredient_id][0], normal[ingredient_id][1], Decimal("0"))
        buffer = (needed * buffer_pct / 100).quantize(QUANTITY)
        target = needed + buffer
        normal_use = normal.get(ingredient_id, (name, unit, Decimal("0")))[2]
        delivered = deliveries.get(ingredient_id, Decimal("0"))
        estimate = estimates.get(ingredient_id)
        known = estimate is not None and estimate.stock_status == "known"
        on_hand = estimate.estimated_quantity if known else None
        available = None if on_hand is None else on_hand + delivered - normal_use
        pack = packs_policy.get(ingredient_id)
        pack_size = Decimal(pack["size"]) if pack else None
        shortfall = packs = None
        if available is None:
            status, order = "unknown_stock", None
        elif available >= target:
            status, order = "enough", Decimal("0")
        else:
            status = "short"
            shortfall = (target - available).quantize(QUANTITY)
            if pack_size:
                packs = int((shortfall / pack_size).to_integral_value(rounding=ROUND_CEILING))
                order = pack_size * packs
            else:
                order = _round_up(shortfall, unit)
        lines.append(
            ShortfallLine(
                ingredient_id=ingredient_id,
                name=name,
                unit=unit,
                needed=needed,
                buffer=buffer,
                needed_with_buffer=target,
                on_hand=on_hand,
                deliveries=delivered,
                normal_use=normal_use,
                available=available,
                shortfall=shortfall,
                pack_size=pack_size,
                pack_name=pack["name"] if pack else None,
                packs=packs,
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
        include_normal_use=params.include_normal_use,
        cover_until=cover_until,
        normal_items=normal_count,
        notes=notes,
        lines=lines,
        order_lines=[
            OrderLine(ingredient_id=line.ingredient_id, quantity=line.order_quantity, unit=line.unit)
            for line in lines
            if line.status == "short"
        ],
        unknown_stock=[line.ingredient_id for line in lines if line.status == "unknown_stock"],
        unmapped_variations=[],
    )
