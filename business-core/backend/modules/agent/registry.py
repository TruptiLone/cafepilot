"""Action registry: the only operations the CaféPilot agent may plan or execute.

Each action wraps an existing domain service method directly (no HTTP hop).
Input and output JSON schemas are generated from Pydantic models, never hand-written.
The planner sees ``describe_for_planner()``; the validator and executor use ``REGISTRY``.

Deliberately NOT registered: ``create_sale`` (POS-only) and ``create_employee`` (HR-only).
Every write action requires owner approval.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator
from sqlalchemy.orm import Session

from backend.modules.purchasing.service import OrderLineIn, offer_shift, send_order
from backend.modules.agent.demand import EventDemandInput, EventDemandResult, estimate_event_demand
from backend.modules.agent.calculations import (
    ShortfallInput,
    ShortfallResult,
    calculate_shortfall,
)
from backend.modules.inventory.schemas import (
    InventoryItemResponse,
    InventoryMovementCreate,
    InventoryMovementResponse,
    StockCountCreate,
    StockCountResponse,
    StockCountSummaryResponse,
    StockCountVerify,
    StockEstimateResponse,
)
from backend.modules.inventory.service import InventoryService
from backend.modules.sales.schemas import (
    MenuProductResponse,
    SalesReportResponse,
    SaleSummaryResponse,
    UsageProjectionRequest,
    UsageProjectionResponse,
)
from backend.modules.sales.service import SalesService
from backend.modules.staffing.schemas import (
    CoverageReportResponse,
    EmployeeResponse,
    ShiftCreate,
    ShiftResponse,
)
from backend.modules.staffing.service import StaffingService

SideEffect = Literal["read", "write"]
Approval = Literal["none", "owner"]
DEFAULT_CAFE_ID = "CAFE001"


# ---------------------------------------------------------------------------
# Input models for actions whose REST form uses query/path parameters
# ---------------------------------------------------------------------------


class _AgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("timestamps must include a timezone offset")
    return value


class CafeScopedInput(_AgentInput):
    cafe_id: str = DEFAULT_CAFE_ID


class ListIngredientsInput(CafeScopedInput):
    category: str | None = None
    include_inactive: bool = False


class StockEstimatesInput(CafeScopedInput):
    ingredient_id: str | None = Field(
        default=None, description="One ingredient. Omit (and omit ingredient_ids) to get every ingredient."
    )
    ingredient_ids: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Only these ingredients, e.g. the ones the request is about.",
    )
    as_of: datetime | None = Field(
        default=None,
        description="Knowledge cutoff with timezone offset. Omit for now.",
    )

    _aware = field_validator("as_of")(_require_aware)


class ListStockCountsInput(CafeScopedInput):
    status: Literal["draft", "verified"] | None = None


class StockCountIdInput(CafeScopedInput):
    count_id: str = Field(min_length=1)


class VerifyStockCountInput(CafeScopedInput):
    count_id: str = Field(min_length=1)
    counted_at: datetime | None = None

    _aware = field_validator("counted_at")(_require_aware)


class ListMovementsInput(CafeScopedInput):
    ingredient_id: str | None = None
    movement_type: str | None = None


class TimeRangeInput(CafeScopedInput):
    start_at: datetime
    end_at: datetime

    _aware = field_validator("start_at", "end_at")(_require_aware)


class OptionalTimeRangeInput(CafeScopedInput):
    start_at: datetime | None = None
    end_at: datetime | None = None

    _aware = field_validator("start_at", "end_at")(_require_aware)


class CoverageInput(TimeRangeInput):
    required_staff: int = Field(default=2, ge=1)


class CancelShiftInput(CafeScopedInput):
    shift_id: str = Field(min_length=1)


class SupplierOrderLine(_AgentInput):
    ingredient_id: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0, description="Quantity in the ingredient's base unit.")
    unit: str = Field(min_length=1, description="Must equal the ingredient's base unit.")


class SupplierOrderInput(CafeScopedInput):
    supplier_id: str | None = Field(
        default=None, description="A supplier_id from REFERENCE DATA suppliers. Omit for the café's usual supplier."
    )
    lines: list[SupplierOrderLine] = Field(min_length=1)
    requested_delivery_at: datetime
    note: str | None = None

    _aware = field_validator("requested_delivery_at")(_require_aware)


class SupplierOrderLineOut(BaseModel):
    ingredient_id: str
    quantity: Decimal
    unit: str
    packs: int | None = None
    pack_name: str | None = None
    pack_price_cents: int | None = None


class GetPurchaseOrderInput(CafeScopedInput):
    order_id: str = Field(min_length=1, description="A purchase order id, e.g. from a rejected order.")


class PurchaseOrderOut(BaseModel):
    order_id: str
    supplier_id: str
    supplier_name: str
    status: str
    response_reason: str | None
    requested_delivery_at: datetime
    confirmed_delivery_at: datetime | None
    lines: list["SupplierOrderLineOut"]
    reorder_lines: list[SupplierOrderLine] = Field(description="The same quantities, ready to send again.")


class SupplierOrderResponse(BaseModel):
    order_id: str
    po_number: str | None = None
    order_ref: str
    supplier_id: str
    supplier_name: str
    status: str
    simulated: bool
    requested_delivery_at: datetime
    confirmed_delivery_at: datetime | None
    lines: list[SupplierOrderLineOut]
    total_cents: int
    message: str


# ---------------------------------------------------------------------------
# Action definition
# ---------------------------------------------------------------------------


Handler = Callable[..., Any]


@dataclass(frozen=True)
class WriteContext:
    """Passed to writes that create records: a stable id plus where the request came from."""

    record_id: str | None
    run_id: str | None = None
    node_id: str | None = None


@dataclass(frozen=True)
class Action:
    name: str
    description: str
    domain: str
    side_effect: SideEffect
    input_model: type[BaseModel]
    output_type: Any
    handler: Handler
    consumes: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    # Each inner tuple is an any-of group; every group must be satisfied upstream.
    requires: tuple[tuple[str, ...], ...] = ()
    approval: Approval = "none"
    # Optional: given resolved args, return a reason to skip the step instead of running it.
    skip_reason: Callable[[dict[str, Any]], str | None] | None = None
    # Writes that create a record: the executor passes a stable id so a retry cannot duplicate it.
    record_id_prefix: str | None = None
    # Writes: return the already-applied result for these args (and record id), or None.
    find_existing: Callable[[Session, Any, str | None], Any] | None = None
    _output_adapter: TypeAdapter = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.side_effect == "write" and self.approval != "owner":
            raise ValueError(f"write action {self.name} must require owner approval")
        object.__setattr__(self, "_output_adapter", TypeAdapter(self.output_type))

    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    def output_schema(self) -> dict[str, Any]:
        return self._output_adapter.json_schema()

    def _dump(self, result: Any) -> Any:
        validated = self._output_adapter.validate_python(result, from_attributes=True)
        return self._output_adapter.dump_python(validated, mode="json")

    def run(
        self,
        session: Session,
        raw_args: dict[str, Any],
        *,
        record_id: str | None = None,
        run_id: str | None = None,
        node_id: str | None = None,
    ) -> Any:
        """Validate args, call the domain service, return JSON-safe output."""
        params = self.input_model.model_validate(raw_args)
        if self.record_id_prefix is not None:
            return self._dump(self.handler(session, params, WriteContext(record_id, run_id, node_id)))
        return self._dump(self.handler(session, params))

    def existing(self, session: Session, raw_args: dict[str, Any], record_id: str | None) -> Any:
        """JSON result of this write if it has already been applied, else None."""
        if self.find_existing is None:
            return None
        params = self.input_model.model_validate(raw_args)
        found = self.find_existing(session, params, record_id)
        return None if found is None else self._dump(found)


def _existing_shift(session: Session, p: ShiftCreate, record_id: str | None) -> Any:
    if not record_id:
        return None
    try:
        return StaffingService(session).get_shift(record_id, p.cafe_id)
    except Exception:
        return None


def _existing_count(session: Session, p: StockCountCreate, record_id: str | None) -> Any:
    if not record_id:
        return None
    try:
        return InventoryService(session).get_count(record_id, p.cafe_id)
    except Exception:
        return None


def _already_verified(session: Session, p: Any, _: str | None) -> Any:
    try:
        count = InventoryService(session).get_count(p.count_id, p.cafe_id)
    except Exception:
        return None
    return count if count.status == "verified" else None


def _already_cancelled(session: Session, p: Any, _: str | None) -> Any:
    try:
        shift = StaffingService(session).get_shift(p.shift_id, p.cafe_id)
    except Exception:
        return None
    return shift if shift.status == "cancelled" else None


# ---------------------------------------------------------------------------
# Handlers (thin wrappers over existing services)
# ---------------------------------------------------------------------------


def _order_response(order: Any) -> SupplierOrderResponse:
    return SupplierOrderResponse(
        order_id=order.order_id,
        po_number=order.po_number,
        order_ref=order.po_number or order.order_id,
        supplier_id=order.supplier_id,
        supplier_name=order.supplier_name,
        status=order.status,
        simulated=order.simulated,
        requested_delivery_at=order.requested_delivery_at,
        confirmed_delivery_at=order.confirmed_delivery_at,
        lines=[
            SupplierOrderLineOut(
                ingredient_id=line.ingredient_id, quantity=line.quantity, unit=line.unit, packs=line.packs,
                pack_name=line.pack_name, pack_price_cents=line.pack_price_cents,
            )
            for line in order.lines
        ],
        total_cents=order.total_cents,
        message="Sent to a simulated supplier; it answers in a few seconds. No real order is placed."
        if order.simulated
        else "Sent to supplier.",
    )


def _get_purchase_order(session: Session, p: GetPurchaseOrderInput) -> PurchaseOrderOut:
    from backend.modules.purchasing.models import PurchaseOrder
    from backend.modules.purchasing.service import settle_due

    settle_due(session, cafe_id=p.cafe_id)
    order = session.get(PurchaseOrder, p.order_id)
    if order is None or order.cafe_id != p.cafe_id:
        raise LookupError(f"purchase order not found: {p.order_id}")
    out = _order_response(order)
    return PurchaseOrderOut(
        order_id=order.order_id, supplier_id=order.supplier_id, supplier_name=order.supplier_name,
        status=order.status, response_reason=order.response_reason,
        requested_delivery_at=order.requested_delivery_at, confirmed_delivery_at=order.confirmed_delivery_at,
        lines=out.lines,
        reorder_lines=[SupplierOrderLine(ingredient_id=l.ingredient_id, quantity=l.quantity, unit=l.unit) for l in order.lines],
    )


def _send_supplier_order(session: Session, p: SupplierOrderInput, ctx: "WriteContext") -> SupplierOrderResponse:
    inventory = InventoryService(session)
    for line in p.lines:
        item = inventory.get_item(line.ingredient_id, p.cafe_id)
        if line.unit != item.base_unit:
            raise ValueError(f"{line.ingredient_id} must be ordered in {item.base_unit}, not {line.unit}")
    order_id = ctx.record_id or "po_" + hashlib.sha256(
        json.dumps(p.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()[:20]
    from backend.modules.purchasing.models import PurchaseOrder

    existing = session.get(PurchaseOrder, order_id)
    if existing is not None:  # same request sent again: return the original order
        return _order_response(existing)
    order = send_order(
        session,
        cafe_id=p.cafe_id,
        supplier_id=p.supplier_id,
        lines=[OrderLineIn(l.ingredient_id, l.quantity, l.unit) for l in p.lines],
        requested_delivery_at=p.requested_delivery_at,
        order_id=order_id,
        source_run_id=ctx.run_id,
        source_node_id=ctx.node_id,
        note=p.note,
    )
    session.commit()
    return _order_response(order)


def _existing_order(session: Session, p: SupplierOrderInput, record_id: str | None) -> Any:
    from backend.modules.purchasing.models import PurchaseOrder

    order = session.get(PurchaseOrder, record_id) if record_id else None
    return _order_response(order) if order is not None else None


def _schedule_and_offer(session: Session, p: ShiftCreate, ctx: "WriteContext") -> Any:
    shift = StaffingService(session).schedule_shift(p, shift_id=ctx.record_id)
    if ctx.run_id is not None:  # the agent offered it: the employee still has to say yes
        offer_shift(
            session, cafe_id=p.cafe_id, shift_id=shift.shift_id, employee_id=p.employee_id,
            source_run_id=ctx.run_id, source_node_id=ctx.node_id,
        )
        session.commit()
    return shift


_ACTIONS: list[Action] = [
    # ---- Inventory: reads
    Action(
        name="list_ingredients",
        description="List the café's ingredient catalog with base units and categories.",
        domain="inventory",
        side_effect="read",
        input_model=ListIngredientsInput,
        output_type=list[InventoryItemResponse],
        handler=lambda s, p: InventoryService(s).list_items(
            cafe_id=p.cafe_id, category=p.category, include_inactive=p.include_inactive
        ),
        produces=("ingredient_catalog",),
    ),
    Action(
        name="get_stock_estimates",
        description=(
            "Estimated stock per ingredient from the latest verified count plus later "
            "movements. Status is known, unknown or inconsistent; unknown is never zero. "
            "Use as_of for a point-in-time view."
        ),
        domain="inventory",
        side_effect="read",
        input_model=StockEstimatesInput,
        output_type=list[StockEstimateResponse],
        handler=lambda s, p: [
            estimate
            for estimate in InventoryService(s).list_stock_estimates(p.cafe_id, p.ingredient_id, p.as_of)
            if p.ingredient_ids is None or estimate.ingredient_id in p.ingredient_ids
        ],
        consumes=("ingredient_catalog",),
        produces=("stock_estimate",),
    ),
    Action(
        name="list_stock_counts",
        description="List physical stock counts (draft or verified).",
        domain="inventory",
        side_effect="read",
        input_model=ListStockCountsInput,
        output_type=list[StockCountSummaryResponse],
        handler=lambda s, p: InventoryService(s).list_counts(p.cafe_id, p.status),
        produces=("stock_count_list",),
    ),
    Action(
        name="get_stock_count",
        description="Get one stock count with its counted lines.",
        domain="inventory",
        side_effect="read",
        input_model=StockCountIdInput,
        output_type=StockCountResponse,
        handler=lambda s, p: InventoryService(s).get_count(p.count_id, p.cafe_id),
        consumes=("stock_count_list",),
        produces=("stock_count",),
    ),
    Action(
        name="list_inventory_movements",
        description="List receipts, waste, adjustments and estimated sale usage.",
        domain="inventory",
        side_effect="read",
        input_model=ListMovementsInput,
        output_type=list[InventoryMovementResponse],
        handler=lambda s, p: InventoryService(s).list_movements(
            p.cafe_id, p.ingredient_id, p.movement_type
        ),
        consumes=("ingredient_catalog",),
        produces=("movement_history",),
    ),
    # ---- Inventory: writes
    Action(
        name="create_stock_count",
        description="Record a physical stock count (draft by default).",
        domain="inventory",
        side_effect="write",
        input_model=StockCountCreate,
        output_type=StockCountResponse,
        handler=lambda s, p, ctx: InventoryService(s).create_count(p, count_id=ctx.record_id),
        record_id_prefix="count",
        find_existing=_existing_count,
        consumes=("ingredient_catalog", "stock_estimate"),
        produces=("stock_count",),
        approval="owner",
    ),
    Action(
        name="verify_stock_count",
        description="Verify a draft stock count so it becomes the new stock anchor.",
        domain="inventory",
        side_effect="write",
        input_model=VerifyStockCountInput,
        output_type=StockCountResponse,
        handler=lambda s, p: InventoryService(s).verify_count(
            p.count_id, StockCountVerify(counted_at=p.counted_at), p.cafe_id
        ),
        find_existing=_already_verified,
        consumes=("stock_count",),
        produces=("verified_stock_count",),
        requires=(("create_stock_count", "get_stock_count"),),
        approval="owner",
    ),
    Action(
        name="record_inventory_movement",
        description="Record an accepted receipt, waste or adjustment (idempotent by source).",
        domain="inventory",
        side_effect="write",
        input_model=InventoryMovementCreate,
        output_type=InventoryMovementResponse,
        handler=lambda s, p: InventoryService(s).create_movement(p)[0],
        consumes=("stock_estimate", "supplier_order"),
        produces=("inventory_movement",),
        requires=(("get_stock_estimates",),),
        approval="owner",
    ),
    # ---- Sales: reads
    Action(
        name="list_products",
        description="List menu products (base product, category, price). Variation ids are in the planning reference data.",
        domain="sales",
        side_effect="read",
        input_model=CafeScopedInput,
        output_type=list[MenuProductResponse],
        handler=lambda s, p: SalesService(s).list_products(p.cafe_id),
        produces=("menu_catalog",),
    ),
    Action(
        name="list_sales",
        description="List completed sales in an optional time range.",
        domain="sales",
        side_effect="read",
        input_model=OptionalTimeRangeInput,
        output_type=list[SaleSummaryResponse],
        handler=lambda s, p: SalesService(s).list_sales(p.cafe_id, p.start_at, p.end_at),
        produces=("sales_history",),
    ),
    Action(
        name="sales_summary",
        description="Revenue, units, top products and hourly sales for a time range.",
        domain="sales",
        side_effect="read",
        input_model=OptionalTimeRangeInput,
        output_type=SalesReportResponse,
        handler=lambda s, p: SalesService(s).report(p.cafe_id, p.start_at, p.end_at),
        produces=("sales_summary",),
    ),
    Action(
        name="project_recipe_usage",
        description=(
            "Read-only: expand a hypothetical product mix (variation ids, quantities, "
            "modifiers such as MILK_OAT) into ingredient quantities using recipes "
            "effective at target_at. Writes nothing."
        ),
        domain="sales",
        side_effect="read",
        input_model=UsageProjectionRequest,
        output_type=UsageProjectionResponse,
        handler=lambda s, p: SalesService(s).project_usage(p),
        consumes=("menu_catalog", "sales_summary"),
        produces=("ingredient_demand",),
    ),
    # ---- Staffing: reads
    Action(
        name="list_employees",
        description="List employees with their single role (e.g. barista, cashier).",
        domain="staffing",
        side_effect="read",
        input_model=CafeScopedInput,
        output_type=list[EmployeeResponse],
        handler=lambda s, p: StaffingService(s).list_employees(p.cafe_id),
        produces=("employee_roster",),
    ),
    Action(
        name="list_shifts",
        description="List scheduled and cancelled shifts overlapping a time range.",
        domain="staffing",
        side_effect="read",
        input_model=TimeRangeInput,
        output_type=list[ShiftResponse],
        handler=lambda s, p: StaffingService(s).list_shifts(p.cafe_id, p.start_at, p.end_at),
        produces=("shift_schedule",),
    ),
    Action(
        name="coverage_report",
        description=(
            "Hourly scheduled head count versus a required_staff threshold. Counts people, "
            "not roles; check roles with list_employees."
        ),
        domain="staffing",
        side_effect="read",
        input_model=CoverageInput,
        output_type=CoverageReportResponse,
        handler=lambda s, p: StaffingService(s).coverage_report(
            p.cafe_id, p.start_at, p.end_at, p.required_staff
        ),
        produces=("coverage",),
    ),
    # ---- Staffing: writes
    Action(
        name="schedule_shift",
        description="Schedule an employee for a shift. Overlapping shifts are rejected.",
        domain="staffing",
        side_effect="write",
        input_model=ShiftCreate,
        output_type=ShiftResponse,
        handler=_schedule_and_offer,
        record_id_prefix="shift",
        find_existing=_existing_shift,
        consumes=("employee_roster", "shift_schedule", "coverage"),
        produces=("scheduled_shift",),
        requires=(("coverage_report",), ("list_employees",), ("list_shifts",)),
        approval="owner",
    ),
    Action(
        name="cancel_shift",
        description="Cancel a scheduled shift.",
        domain="staffing",
        side_effect="write",
        input_model=CancelShiftInput,
        output_type=ShiftResponse,
        handler=lambda s, p: StaffingService(s).cancel_shift(p.shift_id, p.cafe_id),
        find_existing=_already_cancelled,
        consumes=("shift_schedule",),
        produces=("cancelled_shift",),
        requires=(("list_shifts",),),
        approval="owner",
    ),
    # ---- Demand from history (code, not the model)
    Action(
        name="estimate_event_demand",
        description=(
            "Read-only: expected demand for an upcoming event, from sales history. Normal day = average "
            "of recent same-weekday days without events; event effect = what past events of the same "
            "kind did. Returns low / typical / high scenarios whose .items feed calculate_shortfall, "
            "plus how many past examples were used (confidence good / rough / none)."
        ),
        domain="demand",
        side_effect="read",
        input_model=EventDemandInput,
        output_type=EventDemandResult,
        handler=estimate_event_demand,
        consumes=("event_calendar",),
        produces=("event_demand", "ingredient_demand"),
    ),
    # ---- Order math (code, not the model)
    Action(
        name="calculate_shortfall",
        description=(
            "Read-only order math in code. Extra need (recipes x items) + buffer (café policy %) - stock "
            "available = order, per ingredient. With include_normal_use=true, available = stock as_of + "
            "regular deliveries - normal sales until cover_until (default: closing time on the target "
            "day); use it for events. Returns order_lines ready for propose_supplier_order and lists "
            "ingredients whose stock is unknown."
        ),
        domain="purchasing",
        side_effect="read",
        input_model=ShortfallInput,
        output_type=ShortfallResult,
        handler=calculate_shortfall,
        consumes=("menu_catalog", "stock_estimate", "ingredient_demand", "event_demand"),
        produces=("order_suggestion",),
    ),
    Action(
        name="get_purchase_order",
        description=(
            "Read a purchase order: supplier, status (sent / confirmed / rejected), the supplier's reason, and "
            "reorder_lines with the same quantities. Use it to replan after a rejection: send "
            "\"$<this step>.reorder_lines\" to another supplier."
        ),
        domain="purchasing",
        side_effect="read",
        input_model=GetPurchaseOrderInput,
        output_type=PurchaseOrderOut,
        handler=_get_purchase_order,
        consumes=(),
        produces=("order_suggestion", "purchase_order"),
    ),
    # ---- Purchasing (simulated)
    Action(
        name="propose_supplier_order",
        description=(
            "Send an order to a SIMULATED supplier. lines MUST be \"$<calculate_shortfall step>.order_lines\" "
            "so quantities come from code. supplier_id from REFERENCE DATA suppliers (omit for the usual one). "
            "The supplier answers a few seconds later: confirmed, or rejected with a reason. No real order is placed."
        ),
        domain="purchasing",
        side_effect="write",
        input_model=SupplierOrderInput,
        output_type=SupplierOrderResponse,
        handler=_send_supplier_order,
        record_id_prefix="po",
        find_existing=_existing_order,
        consumes=("stock_estimate", "ingredient_demand", "order_suggestion"),
        produces=("supplier_order",),
        requires=(("calculate_shortfall", "get_purchase_order"),),
        approval="owner",
        skip_reason=lambda args: None if args.get("lines") else "Nothing to order: stock covers the need.",
    ),
]

REGISTRY: dict[str, Action] = {action.name: action for action in _ACTIONS}
if len(REGISTRY) != len(_ACTIONS):
    raise RuntimeError("duplicate action names in registry")


def describe_for_planner() -> list[dict[str, Any]]:
    """Compact, deterministic description of every action for the planner prompt."""
    return [
        {
            "name": action.name,
            "description": action.description,
            "side_effect": action.side_effect,
            "needs_owner_approval": action.approval == "owner",
            "input_schema": action.input_schema(),
            "must_come_after": [list(group) for group in action.requires],
        }
        for action in _ACTIONS
    ]
