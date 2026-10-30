"""Purchase orders sent to suppliers, and staff shift offers: the records that make
"approved" different from "confirmed".

An order is ``sent`` until the supplier answers (``confirmed`` or ``rejected``); receiving later
records what actually arrived. A shift offer is ``pending`` until the employee answers.
Demo suppliers and staff replies are simulated and labeled ``simulated``.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text
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
    )

    order_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(Text, ForeignKey("cafes.cafe_id"), nullable=False)
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
