from sqlalchemy import Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base

INITIAL_CAFE_ID = "CAFE001"


class Cafe(Base):
    __tablename__ = "cafes"

    cafe_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(
        Text,
        default="America/Los_Angeles",
        server_default=text("'America/Los_Angeles'"),
        nullable=False,
    )

    inventory_items: Mapped[list["InventoryItem"]] = relationship(
        back_populates="cafe"
    )
    stock_counts: Mapped[list["StockCount"]] = relationship(back_populates="cafe")
    inventory_movements: Mapped[list["InventoryMovement"]] = relationship(
        back_populates="cafe"
    )
