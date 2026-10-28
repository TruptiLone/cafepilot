from collections import defaultdict
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models.cafe import Cafe
from backend.modules.inventory.models import InventoryMovement
from backend.modules.sales.models import (
    MenuProduct,
    Modifier,
    ModifierGroup,
    ProductVariation,
    Sale,
    SaleItem,
    SaleItemModifier,
)
from backend.modules.sales.repository import SalesRepository
from backend.modules.sales.schemas import (
    LegacySaleItemCreate,
    SaleCreate,
    SaleItemV2Create,
)

DEFAULT_CAFE_ID = "CAFE001"
MAX_INTEGER = 2_147_483_647
QUANTITY_SCALE = Decimal("0.001")


class CafeNotFoundError(LookupError):
    pass


class ProductNotFoundError(LookupError):
    pass


class SaleNotFoundError(LookupError):
    pass


class SaleConflictError(RuntimeError):
    pass


class SalesValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SaleItemModifierResult:
    modifier_id: str
    modifier_name: str
    price_cents: int
    quantity: int


@dataclass(frozen=True)
class SaleItemResult:
    sale_item_id: str
    product_id: str
    product_name: str
    variation_id: str
    variation_name: str
    quantity: int
    unit_price_cents: int
    modifier_unit_cents: int
    line_total_cents: int
    modifiers: list[SaleItemModifierResult]


@dataclass(frozen=True)
class SaleResult:
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
    items: list[SaleItemResult]


@dataclass(frozen=True)
class SaleSummaryResult:
    sale_id: str
    cafe_id: str
    source_id: str
    sold_at: datetime
    recorded_at: datetime
    status: str
    total_cents: int
    item_count: int
    total_units: int


@dataclass(frozen=True)
class TopProductResult:
    product_id: str
    name: str
    quantity: int
    revenue_cents: int


@dataclass(frozen=True)
class HourlySalesResult:
    hour: int
    sales_count: int
    units_sold: int
    revenue_cents: int


@dataclass(frozen=True)
class SalesReportResult:
    cafe_id: str
    start_at: datetime | None
    end_at: datetime | None
    total_revenue_cents: int
    sales_count: int
    total_units_sold: int
    average_sale_value_cents: int
    top_products: list[TopProductResult]
    hourly_sales: list[HourlySalesResult]


@dataclass(frozen=True)
class IngredientUsageItemResult:
    ingredient_id: str
    quantity: Decimal
    unit: str


@dataclass(frozen=True)
class IngredientUsageResult:
    sale_id: str
    source: str
    usage_status: str
    movement_posted: bool
    is_estimated: bool
    ingredients: list[IngredientUsageItemResult]
    missing_recipe_variations: list[str]


@dataclass(frozen=True)
class _PreparedModifier:
    modifier: Modifier
    quantity: int


@dataclass(frozen=True)
class _PreparedLine:
    product: MenuProduct
    variation: ProductVariation
    quantity: int
    unit_price_cents: int
    modifiers: tuple[_PreparedModifier, ...]
    modifier_unit_cents: int
    line_total_cents: int
    usage: dict[str, tuple[Decimal, str]] | None


@dataclass(frozen=True)
class _PreparedSale:
    source: str
    external_id: str | None
    discount_cents: int
    subtotal_cents: int
    tax_cents: int
    total_cents: int
    lines: tuple[_PreparedLine, ...]


@dataclass
class _ProductAggregate:
    name: str
    quantity: int = 0
    revenue_cents: int = 0


@dataclass
class _HourlyAggregate:
    sale_ids: set[str]
    units_sold: int = 0
    revenue_cents: int = 0


class SalesService:
    def __init__(self, database_session: Session) -> None:
        self._database_session = database_session
        self._repository = SalesRepository(database_session)

    def list_products(self, cafe_id: str = DEFAULT_CAFE_ID) -> list[MenuProduct]:
        self._require_cafe(cafe_id)
        return self._repository.list_products(cafe_id)

    def create_sale(self, request: SaleCreate) -> tuple[SaleResult, bool]:
        sale_id: str | None = None
        with self._transaction():
            cafe = self._require_cafe(request.cafe_id)
            existing_rows = self._repository.get_sale_by_source(
                request.cafe_id,
                request.source_id,
            )
            if existing_rows:
                return (
                    self._resolve_sale_identity(existing_rows, request),
                    False,
                )

            prepared = self._prepare_sale(cafe, request)
            if prepared.external_id is not None:
                external_rows = self._repository.get_sale_by_external_identity(
                    request.cafe_id,
                    prepared.source,
                    prepared.external_id,
                )
                if external_rows:
                    raise SaleConflictError(
                        "external_id conflicts with an existing sale"
                    )

            sale_id = f"sale_{uuid4().hex}"
            recorded_at = datetime.now(timezone.utc)
            sale = Sale(
                sale_id=sale_id,
                cafe_id=request.cafe_id,
                source_id=request.source_id,
                source=prepared.source,
                external_id=prepared.external_id,
                sold_at=request.sold_at.astimezone(timezone.utc),
                recorded_at=recorded_at,
                status="completed",
                subtotal_cents=prepared.subtotal_cents,
                discount_cents=prepared.discount_cents,
                tax_cents=prepared.tax_cents,
                total_cents=prepared.total_cents,
            )
            sale_items: list[SaleItem] = []
            sale_modifiers: list[SaleItemModifier] = []
            movements: list[InventoryMovement] = []
            for line in prepared.lines:
                sale_item_id = f"sale_item_{uuid4().hex}"
                sale_items.append(
                    SaleItem(
                        sale_item_id=sale_item_id,
                        sale_id=sale_id,
                        cafe_id=request.cafe_id,
                        product_id=line.product.product_id,
                        variation_id=line.variation.variation_id,
                        product_name=line.product.name,
                        variation_name=line.variation.name,
                        quantity=line.quantity,
                        unit_price_cents=line.unit_price_cents,
                        modifier_unit_cents=line.modifier_unit_cents,
                        line_total_cents=line.line_total_cents,
                    )
                )
                sale_modifiers.extend(
                    SaleItemModifier(
                        sale_item_modifier_id=f"sale_modifier_{uuid4().hex}",
                        cafe_id=request.cafe_id,
                        sale_item_id=sale_item_id,
                        modifier_id=selection.modifier.modifier_id,
                        modifier_name=selection.modifier.name,
                        price_cents=selection.modifier.price_cents,
                        quantity=selection.quantity,
                    )
                    for selection in line.modifiers
                )
                if prepared.source == "pos" and line.usage is not None:
                    movements.extend(
                        InventoryMovement(
                            movement_id=f"movement_{uuid4().hex}",
                            cafe_id=request.cafe_id,
                            ingredient_id=ingredient_id,
                            movement_type="SALE_USAGE",
                            quantity_delta=-quantity,
                            is_estimated=True,
                            source_type="sale_item",
                            source_id=sale_item_id,
                            occurred_at=sale.sold_at,
                            recorded_at=recorded_at,
                            recorded_by=None,
                            reason="Estimated recipe consumption",
                        )
                        for ingredient_id, (quantity, _) in line.usage.items()
                    )
            try:
                with self._database_session.begin_nested():
                    self._repository.create_sale(
                        sale,
                        sale_items,
                        sale_modifiers,
                        movements,
                    )
            except IntegrityError:
                existing_rows = self._repository.get_sale_by_source(
                    request.cafe_id,
                    request.source_id,
                )
                if existing_rows:
                    return (
                        self._resolve_sale_identity(
                            existing_rows,
                            request,
                        ),
                        False,
                    )
                if prepared.external_id is not None and self._repository.get_sale_by_external_identity(
                    request.cafe_id,
                    prepared.source,
                    prepared.external_id,
                ):
                    raise SaleConflictError(
                        "external_id conflicts with an existing sale"
                    )
                raise

        if sale_id is None:
            raise RuntimeError("sale was not created")
        return self.get_sale(sale_id, request.cafe_id), True

    def list_sales(
        self,
        cafe_id: str = DEFAULT_CAFE_ID,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> list[SaleSummaryResult]:
        self._require_cafe(cafe_id)
        self._validate_range(start_at, end_at)
        return [
            SaleSummaryResult(
                sale_id=sale.sale_id,
                cafe_id=sale.cafe_id,
                source_id=sale.source_id,
                sold_at=sale.sold_at,
                recorded_at=sale.recorded_at,
                status=sale.status,
                total_cents=sale.total_cents,
                item_count=item_count,
                total_units=total_units,
            )
            for sale, item_count, total_units in self._repository.list_sales(
                cafe_id,
                start_at,
                end_at,
            )
        ]

    def get_sale(self, sale_id: str, cafe_id: str = DEFAULT_CAFE_ID) -> SaleResult:
        self._require_cafe(cafe_id)
        rows = self._repository.get_sale_rows(cafe_id, sale_id)
        if not rows:
            raise SaleNotFoundError(sale_id)
        return self._sale_result(rows)

    def get_ingredient_usage(
        self,
        sale_id: str,
        cafe_id: str = DEFAULT_CAFE_ID,
    ) -> IngredientUsageResult:
        self._require_cafe(cafe_id)
        rows = self._repository.get_sale_rows(cafe_id, sale_id)
        if not rows:
            raise SaleNotFoundError(sale_id)
        sale = rows[0][0]
        items = [item for _, item, _ in rows]
        item_ids = {item.sale_item_id for item in items}
        modifier_rows = self._repository.get_sale_modifiers(item_ids)
        modifiers_by_item: dict[str, list[SaleItemModifier]] = defaultdict(list)
        for modifier in modifier_rows:
            modifiers_by_item[modifier.sale_item_id].append(modifier)

        modifier_models = {
            modifier.modifier_id: modifier
            for modifier in self._repository.get_modifiers_by_ids(
                cafe_id,
                {modifier.modifier_id for modifier in modifier_rows},
            )
        }
        totals: dict[str, tuple[Decimal, str]] = {}
        missing_variations: list[str] = []
        computed_lines = 0
        for item in items:
            selections = []
            invalid_selection = False
            for snapshot in modifiers_by_item[item.sale_item_id]:
                modifier = modifier_models.get(snapshot.modifier_id)
                if modifier is None:
                    invalid_selection = True
                    break
                selections.append(_PreparedModifier(modifier, snapshot.quantity))
            usage = None if invalid_selection else self._calculate_usage(
                cafe_id,
                item.variation_id,
                sale.sold_at,
                item.quantity,
                tuple(selections),
            )
            if usage is None:
                missing_variations.append(item.variation_id)
                continue
            computed_lines += 1
            for ingredient_id, (quantity, unit) in usage.items():
                existing = totals.get(ingredient_id)
                if existing is not None and existing[1] != unit:
                    raise SalesValidationError(
                        f"ingredient unit mismatch: {ingredient_id}"
                    )
                totals[ingredient_id] = (
                    (existing[0] if existing else Decimal("0")) + quantity,
                    unit,
                )

        status = (
            "computed"
            if computed_lines == len(items)
            else "partial" if computed_lines else "unmapped"
        )
        movements = self._repository.get_sale_usage_movements(item_ids)
        return IngredientUsageResult(
            sale_id=sale.sale_id,
            source=sale.source,
            usage_status=status,
            movement_posted=bool(movements),
            is_estimated=True,
            ingredients=[
                IngredientUsageItemResult(ingredient_id, quantity, unit)
                for ingredient_id, (quantity, unit) in sorted(totals.items())
            ],
            missing_recipe_variations=sorted(set(missing_variations)),
        )

    def report(
        self,
        cafe_id: str = DEFAULT_CAFE_ID,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> SalesReportResult:
        cafe = self._require_cafe(cafe_id)
        self._validate_range(start_at, end_at)
        try:
            cafe_timezone = ZoneInfo(cafe.timezone)
        except ZoneInfoNotFoundError as error:
            raise SalesValidationError("cafe timezone is invalid") from error

        rows = self._repository.report_rows(cafe_id, start_at, end_at)
        sales: dict[str, Sale] = {}
        products: dict[str, _ProductAggregate] = {}
        hours: dict[int, _HourlyAggregate] = {}
        for sale, item, _ in rows:
            sales[sale.sale_id] = sale
            product_bucket = products.setdefault(
                item.product_id,
                _ProductAggregate(name=item.product_name),
            )
            product_bucket.quantity += item.quantity
            product_bucket.revenue_cents += item.line_total_cents

            hour = sale.sold_at.astimezone(cafe_timezone).hour
            hour_bucket = hours.setdefault(
                hour,
                _HourlyAggregate(sale_ids=set()),
            )
            hour_bucket.sale_ids.add(sale.sale_id)
            hour_bucket.units_sold += item.quantity
            hour_bucket.revenue_cents += item.line_total_cents

        total_revenue = sum(sale.total_cents for sale in sales.values())
        total_units = sum(item.quantity for _, item, _ in rows)
        top_products = sorted(
            (
                TopProductResult(
                    product_id=product_id,
                    name=values.name,
                    quantity=values.quantity,
                    revenue_cents=values.revenue_cents,
                )
                for product_id, values in products.items()
            ),
            key=lambda product: (
                -product.quantity,
                -product.revenue_cents,
                product.product_id,
            ),
        )[:5]
        hourly_sales = [
            HourlySalesResult(
                hour=hour,
                sales_count=len(values.sale_ids),
                units_sold=values.units_sold,
                revenue_cents=values.revenue_cents,
            )
            for hour, values in sorted(hours.items())
        ]
        return SalesReportResult(
            cafe_id=cafe_id,
            start_at=start_at,
            end_at=end_at,
            total_revenue_cents=total_revenue,
            sales_count=len(sales),
            total_units_sold=total_units,
            average_sale_value_cents=(total_revenue // len(sales) if sales else 0),
            top_products=top_products,
            hourly_sales=hourly_sales,
        )

    def _prepare_sale(self, cafe: Cafe, request: SaleCreate) -> _PreparedSale:
        lines = (
            self._prepare_v2_lines(cafe, request)
            if request.is_v2
            else self._prepare_legacy_lines(cafe, request)
        )
        subtotal_cents = sum(line.line_total_cents for line in lines)
        discount_cents = request.discount_cents or 0
        if subtotal_cents > MAX_INTEGER:
            raise SalesValidationError("sale subtotal exceeds supported range")
        if discount_cents > subtotal_cents:
            raise SalesValidationError("discount cannot exceed subtotal")
        source = request.source or "pos"
        tax_rate_bps = (cafe.tax_rate_bps or 0) if request.is_v2 else 0
        taxable_cents = subtotal_cents - discount_cents
        tax_cents = (taxable_cents * tax_rate_bps + 5000) // 10000
        total_cents = taxable_cents + tax_cents
        if tax_cents > MAX_INTEGER or total_cents > MAX_INTEGER:
            raise SalesValidationError("sale total exceeds supported range")
        return _PreparedSale(
            source=source,
            external_id=request.external_id if request.is_v2 else None,
            discount_cents=discount_cents,
            subtotal_cents=subtotal_cents,
            tax_cents=tax_cents,
            total_cents=total_cents,
            lines=tuple(lines),
        )

    def _prepare_legacy_lines(
        self,
        cafe: Cafe,
        request: SaleCreate,
    ) -> list[_PreparedLine]:
        legacy_items = [
            item for item in request.items if isinstance(item, LegacySaleItemCreate)
        ]
        product_ids = {item.product_id for item in legacy_items}
        catalog = {
            product.product_id: (product, variation)
            for product, variation in self._repository.get_active_regular_catalog(
                cafe.cafe_id,
                product_ids,
            )
        }
        missing_ids = product_ids - catalog.keys()
        if missing_ids:
            raise ProductNotFoundError(next(iter(missing_ids)))
        lines = []
        for item in legacy_items:
            product, variation = catalog[item.product_id]
            line_total = item.quantity * product.price_cents
            if line_total > MAX_INTEGER:
                raise SalesValidationError("sale item total exceeds supported range")
            lines.append(
                _PreparedLine(
                    product=product,
                    variation=variation,
                    quantity=item.quantity,
                    unit_price_cents=product.price_cents,
                    modifiers=(),
                    modifier_unit_cents=0,
                    line_total_cents=line_total,
                    usage=self._calculate_usage(
                        cafe.cafe_id,
                        variation.variation_id,
                        request.sold_at,
                        item.quantity,
                        (),
                    ),
                )
            )
        return lines

    def _prepare_v2_lines(
        self,
        cafe: Cafe,
        request: SaleCreate,
    ) -> list[_PreparedLine]:
        v2_items = [item for item in request.items if isinstance(item, SaleItemV2Create)]
        variation_ids = {item.variation_id for item in v2_items}
        catalog = {
            variation.variation_id: (variation, product)
            for variation, product in self._repository.get_active_variation_catalog(
                cafe.cafe_id,
                variation_ids,
            )
        }
        missing_ids = variation_ids - catalog.keys()
        if missing_ids:
            raise ProductNotFoundError(next(iter(missing_ids)))
        product_ids = {product.product_id for _, product in catalog.values()}
        mappings, modifiers = self._repository.get_active_modifier_catalog(
            cafe.cafe_id,
            product_ids,
        )
        groups_by_product: dict[str, dict[str, ModifierGroup]] = defaultdict(dict)
        for mapping, group in mappings:
            groups_by_product[mapping.product_id][group.group_id] = group
        modifiers_by_id = {modifier.modifier_id: modifier for modifier in modifiers}
        modifiers_by_group: dict[str, list[Modifier]] = defaultdict(list)
        for modifier in modifiers:
            modifiers_by_group[modifier.group_id].append(modifier)

        lines = []
        for item in v2_items:
            variation, product = catalog[item.variation_id]
            if item.product_id is not None and item.product_id != product.product_id:
                raise SalesValidationError(
                    "variation does not belong to the selected product"
                )
            applicable_groups = groups_by_product.get(product.product_id, {})
            selected: list[_PreparedModifier] = []
            for requested_modifier in item.modifiers:
                modifier = modifiers_by_id.get(requested_modifier.modifier_id)
                if (
                    modifier is None
                    or modifier.group_id not in applicable_groups
                ):
                    raise SalesValidationError(
                        "modifier is inactive or not applicable to the product"
                    )
                selected.append(
                    _PreparedModifier(modifier, requested_modifier.quantity)
                )

            for group_id, group in applicable_groups.items():
                group_selections = [
                    selection
                    for selection in selected
                    if selection.modifier.group_id == group_id
                ]
                if not group_selections and group.min_select > 0:
                    defaults = [
                        modifier
                        for modifier in modifiers_by_group[group_id]
                        if modifier.is_default
                    ]
                    if len(defaults) != 1:
                        raise SalesValidationError(
                            f"modifier group requires a selection: {group.name}"
                        )
                    default_selection = _PreparedModifier(defaults[0], 1)
                    selected.append(default_selection)
                    group_selections.append(default_selection)
                if not group.min_select <= len(group_selections) <= group.max_select:
                    raise SalesValidationError(
                        f"modifier selections are invalid for group: {group.name}"
                    )
                if group.selection_type == "single" and len(group_selections) > 1:
                    raise SalesValidationError(
                        f"modifier group allows one selection: {group.name}"
                    )

            selected.sort(key=lambda selection: selection.modifier.modifier_id)
            modifier_unit_cents = sum(
                selection.modifier.price_cents * selection.quantity
                for selection in selected
            )
            unit_total = variation.price_cents + modifier_unit_cents
            line_total = item.quantity * unit_total
            if unit_total > MAX_INTEGER or line_total > MAX_INTEGER:
                raise SalesValidationError("sale item total exceeds supported range")
            usage = self._calculate_usage(
                cafe.cafe_id,
                variation.variation_id,
                request.sold_at,
                item.quantity,
                tuple(selected),
            )
            if request.source == "pos" and usage is None:
                raise SalesValidationError(
                    f"recipe is unavailable for variation: {variation.variation_id}"
                )
            lines.append(
                _PreparedLine(
                    product=product,
                    variation=variation,
                    quantity=item.quantity,
                    unit_price_cents=variation.price_cents,
                    modifiers=tuple(selected),
                    modifier_unit_cents=modifier_unit_cents,
                    line_total_cents=line_total,
                    usage=usage,
                )
            )
        return lines

    def _calculate_usage(
        self,
        cafe_id: str,
        variation_id: str,
        sold_at: datetime,
        line_quantity: int,
        selections: tuple[_PreparedModifier, ...],
    ) -> dict[str, tuple[Decimal, str]] | None:
        recipe_rows = self._repository.get_recipe_lines(
            cafe_id,
            variation_id,
            sold_at.astimezone(timezone.utc),
        )
        if not recipe_rows:
            return None
        quantities: dict[str, Decimal] = {}
        units: dict[str, str] = {}
        for line, item in recipe_rows:
            if (
                not line.quantity.is_finite()
                or line.quantity <= 0
                or line.unit != item.base_unit
            ):
                return None
            quantities[line.ingredient_id] = line.quantity
            units[line.ingredient_id] = line.unit

        modifier_ids = {selection.modifier.modifier_id for selection in selections}
        effects = self._repository.get_modifier_effects(cafe_id, modifier_ids)
        effects_by_modifier: dict[str, list] = defaultdict(list)
        effect_ingredient_ids: set[str] = set()
        for effect in effects:
            effects_by_modifier[effect.modifier_id].append(effect)
            effect_ingredient_ids.add(effect.ingredient_id)
            if effect.target_ingredient_id is not None:
                effect_ingredient_ids.add(effect.target_ingredient_id)
        inventory_items = {
            item.ingredient_id: item
            for item in self._repository.get_inventory_items(
                cafe_id,
                effect_ingredient_ids,
            )
        }
        replaced_targets: set[str] = set()
        for selection in selections:
            modifier_effects = effects_by_modifier.get(
                selection.modifier.modifier_id,
                [],
            )
            if not modifier_effects:
                if selection.modifier.is_default and selection.modifier.price_cents == 0:
                    continue
                return None
            for effect in modifier_effects:
                ingredient = inventory_items.get(effect.ingredient_id)
                if ingredient is None:
                    return None
                if effect.effect == "replace":
                    target_id = effect.target_ingredient_id
                    if (
                        selection.quantity != 1
                        or target_id is None
                        or target_id in replaced_targets
                        or target_id not in quantities
                    ):
                        return None
                    target = inventory_items.get(target_id)
                    if target is None or target.base_unit != ingredient.base_unit:
                        return None
                    replacement_quantity = quantities.pop(target_id)
                    units.pop(target_id)
                    quantities[effect.ingredient_id] = (
                        quantities.get(effect.ingredient_id, Decimal("0"))
                        + replacement_quantity
                    )
                    units[effect.ingredient_id] = ingredient.base_unit
                    replaced_targets.add(target_id)
                elif effect.effect == "add":
                    if (
                        effect.quantity is None
                        or not effect.quantity.is_finite()
                        or effect.quantity <= 0
                        or effect.unit != ingredient.base_unit
                    ):
                        return None
                    quantities[effect.ingredient_id] = (
                        quantities.get(effect.ingredient_id, Decimal("0"))
                        + effect.quantity * selection.quantity
                    )
                    units[effect.ingredient_id] = effect.unit
                else:
                    return None

        return {
            ingredient_id: (
                (quantity * line_quantity).quantize(QUANTITY_SCALE),
                units[ingredient_id],
            )
            for ingredient_id, quantity in quantities.items()
            if quantity > 0
        }

    def _resolve_sale_identity(
        self,
        rows: list[tuple[Sale, SaleItem, MenuProduct]],
        request: SaleCreate,
    ) -> SaleResult:
        sale = rows[0][0]
        items = [item for _, item, _ in rows]
        modifier_rows = self._repository.get_sale_modifiers(
            {item.sale_item_id for item in items}
        )
        modifiers_by_item: dict[str, list[SaleItemModifier]] = defaultdict(list)
        for modifier in modifier_rows:
            modifiers_by_item[modifier.sale_item_id].append(modifier)

        if request.is_v2:
            request_items = [
                item for item in request.items if isinstance(item, SaleItemV2Create)
            ]
            stored_modifier_ids = {
                modifier.modifier_id for modifier in modifier_rows
            }
            modifier_catalog = {
                modifier.modifier_id: modifier
                for modifier in self._repository.get_modifiers_by_ids(
                    request.cafe_id,
                    stored_modifier_ids,
                )
            }
            matches = (
                sale.source == request.source
                and sale.external_id == request.external_id
                and sale.discount_cents == (request.discount_cents or 0)
                and self._v2_retry_items_match(
                    request_items,
                    items,
                    modifiers_by_item,
                    modifier_catalog,
                )
            )
        else:
            requested_config = sorted(
                (item.product_id, item.quantity)
                for item in request.items
                if isinstance(item, LegacySaleItemCreate)
            )
            stored_config = sorted((item.product_id, item.quantity) for item in items)
            matches = (
                sale.source == "pos"
                and sale.external_id is None
                and sale.discount_cents == 0
                and sale.tax_cents == 0
                and not modifier_rows
                and requested_config == stored_config
            )
        if (
            sale.sold_at != request.sold_at.astimezone(timezone.utc)
            or not matches
        ):
            raise SaleConflictError("source_id conflicts with an existing sale")
        return self._sale_result(rows)

    @staticmethod
    def _v2_retry_items_match(
        request_items: list[SaleItemV2Create],
        stored_items: list[SaleItem],
        modifiers_by_item: dict[str, list[SaleItemModifier]],
        modifier_catalog: dict[str, Modifier],
    ) -> bool:
        unmatched_items = list(stored_items)
        for requested in request_items:
            requested_modifiers = {
                modifier.modifier_id: modifier.quantity
                for modifier in requested.modifiers
            }
            match_index = None
            for index, stored in enumerate(unmatched_items):
                if (
                    stored.variation_id != requested.variation_id
                    or stored.quantity != requested.quantity
                    or (
                        requested.product_id is not None
                        and stored.product_id != requested.product_id
                    )
                ):
                    continue
                stored_modifiers = {
                    modifier.modifier_id: modifier.quantity
                    for modifier in modifiers_by_item[stored.sale_item_id]
                }
                if any(
                    stored_modifiers.get(modifier_id) != quantity
                    for modifier_id, quantity in requested_modifiers.items()
                ):
                    continue
                injected_modifier_ids = stored_modifiers.keys() - requested_modifiers.keys()
                if any(
                    modifier_id not in modifier_catalog
                    or not modifier_catalog[modifier_id].is_default
                    for modifier_id in injected_modifier_ids
                ):
                    continue
                if len(stored_modifiers) != (
                    len(requested_modifiers) + len(injected_modifier_ids)
                ):
                    continue
                match_index = index
                break
            if match_index is None:
                return False
            unmatched_items.pop(match_index)
        return not unmatched_items

    def _sale_result(
        self,
        rows: list[tuple[Sale, SaleItem, MenuProduct]],
    ) -> SaleResult:
        sale = rows[0][0]
        item_ids = {item.sale_item_id for _, item, _ in rows}
        modifiers_by_item: dict[str, list[SaleItemModifier]] = defaultdict(list)
        for modifier in self._repository.get_sale_modifiers(item_ids):
            modifiers_by_item[modifier.sale_item_id].append(modifier)
        return SaleResult(
            sale_id=sale.sale_id,
            cafe_id=sale.cafe_id,
            source_id=sale.source_id,
            source=sale.source,
            external_id=sale.external_id,
            sold_at=sale.sold_at,
            recorded_at=sale.recorded_at,
            status=sale.status,
            subtotal_cents=sale.subtotal_cents,
            discount_cents=sale.discount_cents,
            tax_cents=sale.tax_cents,
            total_cents=sale.total_cents,
            items=[
                SaleItemResult(
                    sale_item_id=item.sale_item_id,
                    product_id=item.product_id,
                    product_name=item.product_name,
                    variation_id=item.variation_id,
                    variation_name=item.variation_name,
                    quantity=item.quantity,
                    unit_price_cents=item.unit_price_cents,
                    modifier_unit_cents=item.modifier_unit_cents,
                    line_total_cents=item.line_total_cents,
                    modifiers=[
                        SaleItemModifierResult(
                            modifier_id=modifier.modifier_id,
                            modifier_name=modifier.modifier_name,
                            price_cents=modifier.price_cents,
                            quantity=modifier.quantity,
                        )
                        for modifier in modifiers_by_item[item.sale_item_id]
                    ],
                )
                for _, item, _ in rows
            ],
        )

    def _require_cafe(self, cafe_id: str) -> Cafe:
        cafe = self._repository.get_cafe(cafe_id)
        if cafe is None:
            raise CafeNotFoundError(cafe_id)
        return cafe

    @staticmethod
    def _validate_range(start_at: datetime | None, end_at: datetime | None) -> None:
        for value in (start_at, end_at):
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise SalesValidationError("date filters must include a timezone")
        if start_at is not None and end_at is not None and start_at >= end_at:
            raise SalesValidationError("start_at must be before end_at")

    def _transaction(self) -> AbstractContextManager:
        if self._database_session.in_transaction():
            return self._database_session.begin_nested()
        return self._database_session.begin()
