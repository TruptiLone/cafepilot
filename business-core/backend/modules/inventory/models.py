from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from backend.db.base import Base
from backend.models.cafe import Cafe


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        CheckConstraint(
            "base_unit IN ('g', 'mL', 'each')",
            name="ck_inventory_items_base_unit",
        ),
        CheckConstraint(
            "reorder_point IS NULL OR reorder_point >= 0",
            name="ck_inventory_items_reorder_point_nonnegative",
        ),
        CheckConstraint(
            "par_level IS NULL OR par_level >= 0",
            name="ck_inventory_items_par_level_nonnegative",
        ),
        UniqueConstraint(
            "ingredient_id",
            "cafe_id",
            name="uq_inventory_items_ingredient_cafe",
        ),
        Index("ix_inventory_items_cafe_id_active", "cafe_id", "active"),
    )

    ingredient_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cafes.cafe_id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    base_unit: Mapped[str] = mapped_column(Text, nullable=False)
    reorder_point: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))
    par_level: Mapped[Decimal | None] = mapped_column(Numeric(18, 3))
    is_perishable: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )

    cafe: Mapped["Cafe"] = relationship(back_populates="inventory_items")
    stock_count_lines: Mapped[list["StockCountLine"]] = relationship(
        back_populates="inventory_item",
        foreign_keys="StockCountLine.ingredient_id",
    )
    inventory_movements: Mapped[list["InventoryMovement"]] = relationship(
        back_populates="inventory_item",
        primaryjoin=lambda: InventoryItem.ingredient_id
        == foreign(InventoryMovement.ingredient_id),
        foreign_keys=lambda: [InventoryMovement.ingredient_id],
    )


class StockCount(Base):
    __tablename__ = "stock_counts"
    __table_args__ = (
        UniqueConstraint("count_id", "cafe_id", name="uq_stock_counts_count_cafe"),
        CheckConstraint(
            "status IN ('draft', 'verified', 'void')",
            name="ck_stock_counts_status",
        ),
        CheckConstraint(
            "status <> 'verified' OR counted_at IS NOT NULL",
            name="ck_stock_counts_verified_requires_counted_at",
        ),
        Index("ix_stock_counts_cafe_id_counted_at", "cafe_id", "counted_at"),
    )

    count_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cafes.cafe_id"),
        nullable=False,
    )
    counted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    counted_by: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    is_synthetic: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )

    cafe: Mapped["Cafe"] = relationship(back_populates="stock_counts")
    lines: Mapped[list["StockCountLine"]] = relationship(
        back_populates="stock_count",
        foreign_keys="StockCountLine.count_id",
    )


class StockCountLine(Base):
    __tablename__ = "stock_count_lines"
    __table_args__ = (
        CheckConstraint(
            "counted_quantity >= 0",
            name="ck_stock_count_lines_counted_quantity_nonnegative",
        ),
        UniqueConstraint(
            "count_id",
            "ingredient_id",
            name="uq_stock_count_lines_count_ingredient",
        ),
        ForeignKeyConstraint(
            ["count_id", "cafe_id"],
            ["stock_counts.count_id", "stock_counts.cafe_id"],
            name="fk_stock_count_lines_count_cafe",
        ),
        ForeignKeyConstraint(
            ["ingredient_id", "cafe_id"],
            ["inventory_items.ingredient_id", "inventory_items.cafe_id"],
            name="fk_stock_count_lines_item_cafe",
        ),
    )

    count_line_id: Mapped[str] = mapped_column(Text, primary_key=True)
    count_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("stock_counts.count_id"),
        nullable=False,
    )
    cafe_id: Mapped[str] = mapped_column(Text, nullable=False)
    ingredient_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("inventory_items.ingredient_id"),
        nullable=False,
    )
    counted_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)

    stock_count: Mapped["StockCount"] = relationship(
        back_populates="lines",
        foreign_keys=[count_id],
    )
    inventory_item: Mapped["InventoryItem"] = relationship(
        back_populates="stock_count_lines",
        foreign_keys=[ingredient_id],
    )


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (
        CheckConstraint(
            "(movement_type = 'RECEIPT' AND quantity_delta > 0) "
            "OR (movement_type IN ('WASTE', 'SALE_USAGE') AND quantity_delta < 0) "
            "OR (movement_type = 'ADJUSTMENT' AND quantity_delta <> 0)",
            name="ck_inventory_movements_type_and_quantity_sign",
        ),
        ForeignKeyConstraint(
            ["ingredient_id", "cafe_id"],
            ["inventory_items.ingredient_id", "inventory_items.cafe_id"],
            name="fk_inventory_movements_item_cafe",
        ),
        UniqueConstraint(
            "ingredient_id",
            "source_type",
            "source_id",
            name="uq_inventory_movements_ingredient_source_event",
        ),
        Index(
            "ix_inventory_movements_ingredient_id_occurred_at",
            "ingredient_id",
            "occurred_at",
        ),
    )

    movement_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cafes.cafe_id"),
        nullable=False,
    )
    ingredient_id: Mapped[str] = mapped_column(Text, nullable=False)
    movement_type: Mapped[str] = mapped_column(Text, nullable=False)
    quantity_delta: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    is_estimated: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_by: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)

    cafe: Mapped["Cafe"] = relationship(back_populates="inventory_movements")
    inventory_item: Mapped["InventoryItem"] = relationship(
        back_populates="inventory_movements",
        primaryjoin=lambda: foreign(InventoryMovement.ingredient_id)
        == InventoryItem.ingredient_id,
        foreign_keys=lambda: [InventoryMovement.ingredient_id],
    )
