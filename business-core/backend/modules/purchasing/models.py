"""Purchase orders sent to suppliers, and staff shift offers: the records that make
"approved" different from "confirmed".

An order is ``sent`` until the supplier answers (``confirmed`` or ``rejected``); receiving later
records what actually arrived. A shift offer is ``pending`` until the employee answers.
Demo suppliers and staff replies are simulated and labeled ``simulated``.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base

ORDER_STATUSES = ("sent", "confirmed", "rejected", "cancelled", "partially_received", "received")
OFFER_STATUSES = ("pending", "accepted", "declined")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        CheckConstraint(_in("status", ORDER_STATUSES), name="ck_purchase_orders_status"),
        CheckConstraint(
            "pending_decision IS NULL OR pending_decision IN ('confirmed', 'rejected')",
            name="ck_purchase_orders_pending_decision",
        ),
        CheckConstraint("total_cents >= 0", name="ck_purchase_orders_total"),
        Index("ix_purchase_orders_cafe_status", "cafe_id", "status"),
        Index("ix_purchase_orders_source", "source_run_id", "source_node_id"),
        UniqueConstraint("cafe_id", "po_number", name="uq_purchase_orders_cafe_po_number"),
    )

    order_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(Text, ForeignKey("cafes.cafe_id"), nullable=False)
    po_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    supplier_id: Mapped[str] = mapped_column(Text, nullable=False)
    supplier_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    requested_delivery_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    respond_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The simulator decides when the order is sent and reveals it at respond_at.
    pending_decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_node_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    lines: Mapped[list["PurchaseOrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="PurchaseOrderLine.line_no"
    )


class PurchaseOrderLine(Base):
    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_purchase_order_lines_quantity"),
        CheckConstraint("packs IS NULL OR packs > 0", name="ck_purchase_order_lines_packs"),
        CheckConstraint("received_quantity IS NULL OR received_quantity >= 0", name="ck_purchase_order_lines_received"),
    )

    order_id: Mapped[str] = mapped_column(Text, ForeignKey("purchase_orders.order_id", ondelete="CASCADE"), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    ingredient_id: Mapped[str] = mapped_column(Text, nullable=False)
    supplier_item_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    pack_size: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    pack_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    packs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pack_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    received_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)

    order: Mapped[PurchaseOrder] = relationship(back_populates="lines")


class ShiftOffer(Base):
    __tablename__ = "shift_offers"
    __table_args__ = (
        CheckConstraint(_in("status", OFFER_STATUSES), name="ck_shift_offers_status"),
        CheckConstraint(
            "pending_decision IS NULL OR pending_decision IN ('accepted', 'declined')",
            name="ck_shift_offers_pending_decision",
        ),
    )

    shift_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(Text, ForeignKey("cafes.cafe_id"), nullable=False)
    employee_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    offered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    respond_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pending_decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_node_id: Mapped[str | None] = mapped_column(Text, nullable=True)


RECEIPT_STATUSES = ("draft", "confirmed", "cancelled")
LINE_STATUSES = (
    "match",          # ordered = shipped = counted
    "needs_count",    # some units may be hidden (crate, closed case): the owner must count
    "short",          # fewer arrived than the slip says
    "short_shipped",  # the slip itself ships less than was ordered (back-order)
    "over",           # more arrived than ordered
    "not_on_order",   # on the slip or in the photo, but not on our order (wrong item)
    "missing",        # on our order, but neither on the slip nor seen
    "damaged",        # owner rejected some units as damaged or out of date
)


class Receipt(Base):
    """One delivery being checked in: what the slip says, what the photo shows, what the owner counts."""

    __tablename__ = "receipts"
    __table_args__ = (
        CheckConstraint(_in("status", RECEIPT_STATUSES), name="ck_receipts_status"),
        Index("ix_receipts_cafe_status", "cafe_id", "status"),
    )

    receipt_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(Text, ForeignKey("cafes.cafe_id"), nullable=False)
    order_id: Mapped[str | None] = mapped_column(Text, ForeignKey("purchase_orders.order_id"), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    slip_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    slip_supplier: Mapped[str | None] = mapped_column(Text, nullable=True)
    slip_po_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    slip_delivery: Mapped[str | None] = mapped_column(Text, nullable=True)
    slip_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    slip_read: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    photo_read: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel: Mapped[str | None] = mapped_column(Text, nullable=True)
    credit_request: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    lines: Mapped[list["ReceiptLine"]] = relationship(
        back_populates="receipt", cascade="all, delete-orphan", order_by="ReceiptLine.line_no"
    )


class ReceiptLine(Base):
    __tablename__ = "receipt_lines"
    __table_args__ = (
        CheckConstraint(_in("status", LINE_STATUSES), name="ck_receipt_lines_status"),
        CheckConstraint("counted_quantity IS NULL OR counted_quantity >= 0", name="ck_receipt_lines_counted"),
        CheckConstraint("rejected_quantity >= 0", name="ck_receipt_lines_rejected"),
        CheckConstraint("accepted_quantity IS NULL OR accepted_quantity >= 0", name="ck_receipt_lines_accepted"),
    )

    receipt_id: Mapped[str] = mapped_column(Text, ForeignKey("receipts.receipt_id", ondelete="CASCADE"), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    ingredient_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_line_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    supplier_item_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    pack_size: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    unit_size: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    ordered_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    slip_shipped_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    slip_backordered_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    seen_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    seen_may_hide_more: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    counted_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    rejected_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False, default=0)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 3), nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    receipt: Mapped[Receipt] = relationship(back_populates="lines")
