from sqlalchemy import CheckConstraint, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base

INITIAL_CAFE_ID = "CAFE001"


class Cafe(Base):
    __tablename__ = "cafes"
    __table_args__ = (
        CheckConstraint(
            "tax_rate_bps IS NULL OR (tax_rate_bps >= 0 AND tax_rate_bps <= 10000)",
            name="ck_cafes_tax_rate_bps_range",
        ),
    )

    cafe_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(
        Text,
        default="America/Los_Angeles",
        server_default=text("'America/Los_Angeles'"),
        nullable=False,
    )
    tax_rate_bps: Mapped[int | None] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=True,
    )

    inventory_items: Mapped[list["InventoryItem"]] = relationship(
        back_populates="cafe"
    )
    stock_counts: Mapped[list["StockCount"]] = relationship(back_populates="cafe")
    inventory_movements: Mapped[list["InventoryMovement"]] = relationship(
        back_populates="cafe"
    )
    menu_products: Mapped[list["MenuProduct"]] = relationship(back_populates="cafe")
    sales: Mapped[list["Sale"]] = relationship(back_populates="cafe")
