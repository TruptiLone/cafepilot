"""extend sales with historical snapshots

Revision ID: d4e8f1a2c6b9
Revises: a6f3c2d91b47
Create Date: 2026-10-28 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4e8f1a2c6b9"
down_revision: Union[str, Sequence[str], None] = "a6f3c2d91b47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cafes",
        sa.Column(
            "tax_rate_bps",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_cafes_tax_rate_bps_range",
        "cafes",
        "tax_rate_bps IS NULL OR (tax_rate_bps >= 0 AND tax_rate_bps <= 10000)",
    )

    op.add_column(
        "sales",
        sa.Column(
            "source",
            sa.Text(),
            server_default=sa.text("'pos'"),
            nullable=False,
        ),
    )
    op.add_column("sales", sa.Column("external_id", sa.Text(), nullable=True))
    op.add_column("sales", sa.Column("subtotal_cents", sa.Integer(), nullable=True))
    op.add_column(
        "sales",
        sa.Column(
            "discount_cents",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "sales",
        sa.Column(
            "tax_cents",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.execute("UPDATE sales SET subtotal_cents = total_cents")
    op.execute(
        "UPDATE sales SET source = 'generated', external_id = source_id "
        "WHERE left(sale_id, 10) = 'DEMO_SALE_' "
        "OR left(source_id, 9) = 'DEMO_POS_'"
    )
    op.alter_column(
        "sales",
        "subtotal_cents",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_sales_source",
        "sales",
        "source IN ('pos', 'generated', 'csv_import')",
    )
    op.create_check_constraint(
        "ck_sales_subtotal_nonnegative",
        "sales",
        "subtotal_cents >= 0",
    )
    op.create_check_constraint(
        "ck_sales_discount_nonnegative",
        "sales",
        "discount_cents >= 0",
    )
    op.create_check_constraint(
        "ck_sales_discount_within_subtotal",
        "sales",
        "discount_cents <= subtotal_cents",
    )
    op.create_check_constraint(
        "ck_sales_tax_nonnegative",
        "sales",
        "tax_cents >= 0",
    )
    op.create_check_constraint(
        "ck_sales_total_matches_components",
        "sales",
        "total_cents = subtotal_cents - discount_cents + tax_cents",
    )
    op.create_index(
        "uq_sales_cafe_source_external",
        "sales",
        ["cafe_id", "source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )

    op.add_column("sale_items", sa.Column("variation_id", sa.Text(), nullable=True))
    op.add_column("sale_items", sa.Column("product_name", sa.Text(), nullable=True))
    op.add_column("sale_items", sa.Column("variation_name", sa.Text(), nullable=True))
    op.add_column(
        "sale_items",
        sa.Column(
            "modifier_unit_cents",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.execute(
        """
        INSERT INTO product_variations (
            variation_id,
            cafe_id,
            product_id,
            code,
            name,
            size_oz,
            price_cents,
            active
        )
        SELECT
            product.product_id || '_REG',
            product.cafe_id,
            product.product_id,
            'REG',
            'Regular',
            NULL,
            product.price_cents,
            true
        FROM menu_products AS product
        WHERE NOT EXISTS (
            SELECT 1
            FROM product_variations AS variation
            WHERE variation.cafe_id = product.cafe_id
              AND variation.product_id = product.product_id
              AND variation.code = 'REG'
        )
        """
    )
    op.execute(
        """
        UPDATE sale_items AS item
        SET variation_id = variation.variation_id,
            product_name = product.name,
            variation_name = variation.name,
            modifier_unit_cents = 0
        FROM menu_products AS product
        JOIN product_variations AS variation
          ON variation.product_id = product.product_id
         AND variation.cafe_id = product.cafe_id
         AND variation.code = 'REG'
        WHERE item.product_id = product.product_id
          AND item.cafe_id = product.cafe_id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM sale_items
                WHERE variation_id IS NULL
                   OR product_name IS NULL
                   OR variation_name IS NULL
            ) THEN
                RAISE EXCEPTION 'M2 sale item snapshot backfill is incomplete';
            END IF;
        END
        $$
        """
    )
    op.alter_column(
        "sale_items",
        "variation_id",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.alter_column(
        "sale_items",
        "product_name",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.alter_column(
        "sale_items",
        "variation_name",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.drop_constraint(
        "ck_sale_items_line_total_matches",
        "sale_items",
        type_="check",
    )
    op.drop_constraint(
        "uq_sale_items_sale_product",
        "sale_items",
        type_="unique",
    )
    op.create_check_constraint(
        "ck_sale_items_line_total_matches",
        "sale_items",
        "line_total_cents = quantity * (unit_price_cents + modifier_unit_cents)",
    )
    op.create_check_constraint(
        "ck_sale_items_modifier_unit_nonnegative",
        "sale_items",
        "modifier_unit_cents >= 0",
    )
    op.create_foreign_key(
        "fk_sale_items_variation_cafe",
        "sale_items",
        "product_variations",
        ["variation_id", "cafe_id"],
        ["variation_id", "cafe_id"],
    )
    op.create_unique_constraint(
        "uq_sale_items_item_cafe",
        "sale_items",
        ["sale_item_id", "cafe_id"],
    )
    op.create_index(
        "ix_sale_items_cafe_id_sale_id_variation_id",
        "sale_items",
        ["cafe_id", "sale_id", "variation_id"],
        unique=False,
    )

    op.create_table(
        "sale_item_modifiers",
        sa.Column("sale_item_modifier_id", sa.Text(), nullable=False),
        sa.Column("cafe_id", sa.Text(), nullable=False),
        sa.Column("sale_item_id", sa.Text(), nullable=False),
        sa.Column("modifier_id", sa.Text(), nullable=False),
        sa.Column("modifier_name", sa.Text(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column(
            "quantity",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "price_cents >= 0",
            name="ck_sale_item_modifiers_price_nonnegative",
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name="ck_sale_item_modifiers_quantity_positive",
        ),
        sa.ForeignKeyConstraint(["cafe_id"], ["cafes.cafe_id"]),
        sa.ForeignKeyConstraint(
            ["modifier_id", "cafe_id"],
            ["modifiers.modifier_id", "modifiers.cafe_id"],
            name="fk_sale_item_modifiers_modifier_cafe",
        ),
        sa.ForeignKeyConstraint(
            ["sale_item_id", "cafe_id"],
            ["sale_items.sale_item_id", "sale_items.cafe_id"],
            name="fk_sale_item_modifiers_item_cafe",
        ),
        sa.PrimaryKeyConstraint("sale_item_modifier_id"),
        sa.UniqueConstraint(
            "cafe_id",
            "sale_item_id",
            "modifier_id",
            name="uq_sale_item_modifiers_cafe_item_modifier",
        ),
    )
    op.create_index(
        "ix_sale_item_modifiers_cafe_id_sale_item_id",
        "sale_item_modifiers",
        ["cafe_id", "sale_item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sale_item_modifiers_cafe_id_sale_item_id",
        table_name="sale_item_modifiers",
    )
    op.drop_table("sale_item_modifiers")

    op.drop_index(
        "ix_sale_items_cafe_id_sale_id_variation_id",
        table_name="sale_items",
    )
    op.drop_constraint(
        "uq_sale_items_item_cafe",
        "sale_items",
        type_="unique",
    )
    op.drop_constraint(
        "fk_sale_items_variation_cafe",
        "sale_items",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_sale_items_modifier_unit_nonnegative",
        "sale_items",
        type_="check",
    )
    op.drop_constraint(
        "ck_sale_items_line_total_matches",
        "sale_items",
        type_="check",
    )
    op.create_check_constraint(
        "ck_sale_items_line_total_matches",
        "sale_items",
        "line_total_cents = quantity * unit_price_cents",
    )
    op.create_unique_constraint(
        "uq_sale_items_sale_product",
        "sale_items",
        ["sale_id", "product_id"],
    )
    op.drop_column("sale_items", "modifier_unit_cents")
    op.drop_column("sale_items", "variation_name")
    op.drop_column("sale_items", "product_name")
    op.drop_column("sale_items", "variation_id")

    op.drop_index("uq_sales_cafe_source_external", table_name="sales")
    op.drop_constraint(
        "ck_sales_total_matches_components",
        "sales",
        type_="check",
    )
    op.drop_constraint("ck_sales_tax_nonnegative", "sales", type_="check")
    op.drop_constraint(
        "ck_sales_discount_within_subtotal",
        "sales",
        type_="check",
    )
    op.drop_constraint(
        "ck_sales_discount_nonnegative",
        "sales",
        type_="check",
    )
    op.drop_constraint(
        "ck_sales_subtotal_nonnegative",
        "sales",
        type_="check",
    )
    op.drop_constraint("ck_sales_source", "sales", type_="check")
    op.drop_column("sales", "tax_cents")
    op.drop_column("sales", "discount_cents")
    op.drop_column("sales", "subtotal_cents")
    op.drop_column("sales", "external_id")
    op.drop_column("sales", "source")

    op.drop_constraint("ck_cafes_tax_rate_bps_range", "cafes", type_="check")
    op.drop_column("cafes", "tax_rate_bps")
