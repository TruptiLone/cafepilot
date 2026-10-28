from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from backend.db.base import Base


class MenuProduct(Base):
    __tablename__ = "menu_products"
    __table_args__ = (
        CheckConstraint("price_cents >= 0", name="ck_menu_products_price_nonnegative"),
        UniqueConstraint("product_id", "cafe_id", name="uq_menu_products_product_cafe"),
        Index("ix_menu_products_cafe_id_active", "cafe_id", "active"),
    )

    product_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(Text, ForeignKey("cafes.cafe_id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )

    cafe: Mapped["Cafe"] = relationship(back_populates="menu_products")
    sale_items: Mapped[list["SaleItem"]] = relationship(
        back_populates="product",
        primaryjoin=lambda: MenuProduct.product_id == foreign(SaleItem.product_id),
        foreign_keys=lambda: [SaleItem.product_id],
    )


class Sale(Base):
    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint("status IN ('completed', 'void')", name="ck_sales_status"),
        CheckConstraint("total_cents >= 0", name="ck_sales_total_nonnegative"),
        UniqueConstraint("cafe_id", "source_id", name="uq_sales_cafe_source"),
        UniqueConstraint("sale_id", "cafe_id", name="uq_sales_sale_cafe"),
        Index("ix_sales_cafe_id_sold_at", "cafe_id", "sold_at"),
    )

    sale_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(Text, ForeignKey("cafes.cafe_id"), nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    cafe: Mapped["Cafe"] = relationship(back_populates="sales")
    items: Mapped[list["SaleItem"]] = relationship(
        back_populates="sale",
        primaryjoin=lambda: Sale.sale_id == foreign(SaleItem.sale_id),
        foreign_keys=lambda: [SaleItem.sale_id],
    )


class SaleItem(Base):
    __tablename__ = "sale_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_sale_items_quantity_positive"),
        CheckConstraint("unit_price_cents >= 0", name="ck_sale_items_unit_price_nonnegative"),
        CheckConstraint("line_total_cents >= 0", name="ck_sale_items_line_total_nonnegative"),
        CheckConstraint(
            "line_total_cents = quantity * unit_price_cents",
            name="ck_sale_items_line_total_matches",
        ),
        ForeignKeyConstraint(
            ["sale_id", "cafe_id"],
            ["sales.sale_id", "sales.cafe_id"],
            name="fk_sale_items_sale_cafe",
        ),
        ForeignKeyConstraint(
            ["product_id", "cafe_id"],
            ["menu_products.product_id", "menu_products.cafe_id"],
            name="fk_sale_items_product_cafe",
        ),
        UniqueConstraint("sale_id", "product_id", name="uq_sale_items_sale_product"),
    )

    sale_item_id: Mapped[str] = mapped_column(Text, primary_key=True)
    sale_id: Mapped[str] = mapped_column(Text, nullable=False)
    cafe_id: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    sale: Mapped["Sale"] = relationship(
        back_populates="items",
        primaryjoin=lambda: foreign(SaleItem.sale_id) == Sale.sale_id,
        foreign_keys=lambda: [SaleItem.sale_id],
    )
    product: Mapped["MenuProduct"] = relationship(
        back_populates="sale_items",
        primaryjoin=lambda: foreign(SaleItem.product_id) == MenuProduct.product_id,
        foreign_keys=lambda: [SaleItem.product_id],
    )
