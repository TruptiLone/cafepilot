from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models.cafe import Cafe
from backend.modules.sales.models import MenuProduct, Sale, SaleItem
from backend.modules.sales.repository import SalesRepository
from backend.modules.sales.schemas import SaleCreate

DEFAULT_CAFE_ID = "CAFE001"
MAX_INTEGER = 2_147_483_647


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
class SaleItemResult:
    product_id: str
    product_name: str
    quantity: int
    unit_price_cents: int
    line_total_cents: int


@dataclass(frozen=True)
class SaleResult:
    sale_id: str
    cafe_id: str
    source_id: str
    sold_at: datetime
    recorded_at: datetime
    status: str
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
        with self._transaction():
            self._require_cafe(request.cafe_id)
            existing_rows = self._repository.get_sale_by_source(request.cafe_id, request.source_id)
            if existing_rows:
                return self._resolve_sale_identity(existing_rows, request), False

            product_ids = {item.product_id for item in request.items}
            catalog_rows = self._repository.get_active_regular_catalog(
                request.cafe_id,
                product_ids,
            )
            catalog_by_product_id = {
                product.product_id: (product, variation)
                for product, variation in catalog_rows
            }
            missing_ids = product_ids - catalog_by_product_id.keys()
            if missing_ids:
                raise ProductNotFoundError(next(iter(missing_ids)))

            sale_id = f"sale_{uuid4().hex}"
            sale_items = []
            for item in request.items:
                product, variation = catalog_by_product_id[item.product_id]
                sale_items.append(
                    SaleItem(
                        sale_item_id=f"sale_item_{uuid4().hex}",
                        sale_id=sale_id,
                        cafe_id=request.cafe_id,
                        product_id=item.product_id,
                        variation_id=variation.variation_id,
                        product_name=product.name,
                        variation_name=variation.name,
                        quantity=item.quantity,
                        unit_price_cents=product.price_cents,
                        modifier_unit_cents=0,
                        line_total_cents=item.quantity * product.price_cents,
                    )
                )
            if any(item.line_total_cents > MAX_INTEGER for item in sale_items):
                raise SalesValidationError("sale item total exceeds supported range")
            total_cents = sum(item.line_total_cents for item in sale_items)
            if total_cents > MAX_INTEGER:
                raise SalesValidationError("sale total exceeds supported range")
            sale = Sale(
                sale_id=sale_id,
                cafe_id=request.cafe_id,
                source_id=request.source_id,
                source="pos",
                external_id=None,
                sold_at=request.sold_at.astimezone(timezone.utc),
                recorded_at=datetime.now(timezone.utc),
                status="completed",
                subtotal_cents=total_cents,
                discount_cents=0,
                tax_cents=0,
                total_cents=total_cents,
            )
            try:
                with self._database_session.begin_nested():
                    self._repository.create_sale(sale, sale_items)
            except IntegrityError:
                existing_rows = self._repository.get_sale_by_source(
                    request.cafe_id,
                    request.source_id,
                )
                if not existing_rows:
                    raise
                return self._resolve_sale_identity(existing_rows, request), False

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
        for sale, item, product in rows:
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
            key=lambda product: (-product.quantity, -product.revenue_cents, product.product_id),
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

    def _resolve_sale_identity(
        self,
        rows: list[tuple[Sale, SaleItem, MenuProduct]],
        request: SaleCreate,
    ) -> SaleResult:
        sale = rows[0][0]
        existing_items = {item.product_id: item.quantity for _, item, _ in rows}
        requested_items = {item.product_id: item.quantity for item in request.items}
        if sale.sold_at != request.sold_at or existing_items != requested_items:
            raise SaleConflictError("source_id conflicts with an existing sale")
        return self._sale_result(rows)

    @staticmethod
    def _sale_result(rows: list[tuple[Sale, SaleItem, MenuProduct]]) -> SaleResult:
        sale = rows[0][0]
        return SaleResult(
            sale_id=sale.sale_id,
            cafe_id=sale.cafe_id,
            source_id=sale.source_id,
            sold_at=sale.sold_at,
            recorded_at=sale.recorded_at,
            status=sale.status,
            total_cents=sale.total_cents,
            items=[
                SaleItemResult(
                    product_id=item.product_id,
                    product_name=item.product_name,
                    quantity=item.quantity,
                    unit_price_cents=item.unit_price_cents,
                    line_total_cents=item.line_total_cents,
                )
                for _, item, product in rows
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
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise SalesValidationError("date filters must include a timezone")
        if start_at is not None and end_at is not None and start_at >= end_at:
            raise SalesValidationError("start_at must be before end_at")

    def _transaction(self) -> AbstractContextManager:
        if self._database_session.in_transaction():
            return self._database_session.begin_nested()
        return self._database_session.begin()
