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
    false,
    text,
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
    variations: Mapped[list["ProductVariation"]] = relationship(
        back_populates="product"
    )
    modifier_group_links: Mapped[list["ProductModifierGroup"]] = relationship(
        back_populates="product",
        viewonly=True,
    )


class ProductVariation(Base):
    __tablename__ = "product_variations"
    __table_args__ = (
        CheckConstraint(
            "code IN ('REG', 'LRG')",
            name="ck_product_variations_code",
        ),
        CheckConstraint(
            "size_oz IS NULL OR size_oz > 0",
            name="ck_product_variations_size_positive",
        ),
        CheckConstraint(
            "price_cents >= 0",
            name="ck_product_variations_price_nonnegative",
        ),
        ForeignKeyConstraint(
            ["product_id", "cafe_id"],
            ["menu_products.product_id", "menu_products.cafe_id"],
            name="fk_product_variations_product_cafe",
        ),
        UniqueConstraint(
            "variation_id",
            "cafe_id",
            name="uq_product_variations_variation_cafe",
        ),
        UniqueConstraint(
            "cafe_id",
            "product_id",
            "code",
            name="uq_product_variations_cafe_product_code",
        ),
        Index(
            "ix_product_variations_cafe_id_product_id_active",
            "cafe_id",
            "product_id",
            "active",
        ),
    )

    variation_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cafes.cafe_id"),
        nullable=False,
    )
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    size_oz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )

    product: Mapped["MenuProduct"] = relationship(back_populates="variations")


class ModifierGroup(Base):
    __tablename__ = "modifier_groups"
    __table_args__ = (
        CheckConstraint(
            "selection_type IN ('single', 'multiple')",
            name="ck_modifier_groups_selection_type",
        ),
        CheckConstraint(
            "min_select >= 0",
            name="ck_modifier_groups_min_select_nonnegative",
        ),
        CheckConstraint(
            "max_select >= 1",
            name="ck_modifier_groups_max_select_positive",
        ),
        CheckConstraint(
            "max_select >= min_select",
            name="ck_modifier_groups_selection_range",
        ),
        UniqueConstraint(
            "group_id",
            "cafe_id",
            name="uq_modifier_groups_group_cafe",
        ),
        UniqueConstraint(
            "cafe_id",
            "name",
            name="uq_modifier_groups_cafe_name",
        ),
        Index("ix_modifier_groups_cafe_id_active", "cafe_id", "active"),
    )

    group_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cafes.cafe_id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    selection_type: Mapped[str] = mapped_column(Text, nullable=False)
    min_select: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    max_select: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )

    modifiers: Mapped[list["Modifier"]] = relationship(back_populates="group")
    product_links: Mapped[list["ProductModifierGroup"]] = relationship(
        back_populates="group",
        viewonly=True,
    )


class Modifier(Base):
    __tablename__ = "modifiers"
    __table_args__ = (
        CheckConstraint(
            "price_cents >= 0",
            name="ck_modifiers_price_nonnegative",
        ),
        ForeignKeyConstraint(
            ["group_id", "cafe_id"],
            ["modifier_groups.group_id", "modifier_groups.cafe_id"],
            name="fk_modifiers_group_cafe",
        ),
        UniqueConstraint(
            "modifier_id",
            "cafe_id",
            name="uq_modifiers_modifier_cafe",
        ),
        UniqueConstraint(
            "cafe_id",
            "group_id",
            "name",
            name="uq_modifiers_cafe_group_name",
        ),
        Index(
            "ix_modifiers_cafe_id_group_id_active",
            "cafe_id",
            "group_id",
            "active",
        ),
        Index(
            "uq_modifiers_cafe_group_default",
            "cafe_id",
            "group_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    modifier_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cafes.cafe_id"),
        nullable=False,
    )
    group_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    is_default: Mapped[bool] = mapped_column(
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

    group: Mapped["ModifierGroup"] = relationship(back_populates="modifiers")


class ProductModifierGroup(Base):
    __tablename__ = "product_modifier_groups"
    __table_args__ = (
        ForeignKeyConstraint(
            ["product_id", "cafe_id"],
            ["menu_products.product_id", "menu_products.cafe_id"],
            name="fk_product_modifier_groups_product_cafe",
        ),
        ForeignKeyConstraint(
            ["group_id", "cafe_id"],
            ["modifier_groups.group_id", "modifier_groups.cafe_id"],
            name="fk_product_modifier_groups_group_cafe",
        ),
        UniqueConstraint(
            "cafe_id",
            "product_id",
            "group_id",
            name="uq_product_modifier_groups_cafe_product_group",
        ),
        Index(
            "ix_product_modifier_groups_cafe_id_group_id",
            "cafe_id",
            "group_id",
        ),
    )

    product_modifier_group_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("cafes.cafe_id"),
        nullable=False,
    )
    product_id: Mapped[str] = mapped_column(Text, nullable=False)
    group_id: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )

    product: Mapped["MenuProduct"] = relationship(
        back_populates="modifier_group_links",
        viewonly=True,
    )
    group: Mapped["ModifierGroup"] = relationship(
        back_populates="product_links",
        viewonly=True,
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
