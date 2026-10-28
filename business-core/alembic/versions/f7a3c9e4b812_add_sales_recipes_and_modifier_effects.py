"""add sales recipes and modifier effects

Revision ID: f7a3c9e4b812
Revises: d4e8f1a2c6b9
Create Date: 2026-10-28 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7a3c9e4b812"
down_revision: Union[str, Sequence[str], None] = "d4e8f1a2c6b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_sale_items_variation_cafe",
        "sale_items",
        type_="foreignkey",
    )
    op.create_unique_constraint(
        "uq_product_variations_variation_product_cafe",
        "product_variations",
        ["variation_id", "product_id", "cafe_id"],
    )
    op.create_foreign_key(
        "fk_sale_items_variation_product_cafe",
        "sale_items",
        "product_variations",
        ["variation_id", "product_id", "cafe_id"],
        ["variation_id", "product_id", "cafe_id"],
    )

    op.create_table(
        "recipe_lines",
        sa.Column("recipe_line_id", sa.Text(), nullable=False),
        sa.Column("cafe_id", sa.Text(), nullable=False),
        sa.Column("variation_id", sa.Text(), nullable=False),
        sa.Column("ingredient_id", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("unit", sa.Text(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "quantity > 0",
            name="ck_recipe_lines_quantity_positive",
        ),
        sa.CheckConstraint(
            "unit IN ('g', 'mL', 'each')",
            name="ck_recipe_lines_unit",
        ),
        sa.ForeignKeyConstraint(["cafe_id"], ["cafes.cafe_id"]),
        sa.ForeignKeyConstraint(
            ["ingredient_id", "cafe_id"],
            ["inventory_items.ingredient_id", "inventory_items.cafe_id"],
            name="fk_recipe_lines_ingredient_cafe",
        ),
        sa.ForeignKeyConstraint(
            ["variation_id", "cafe_id"],
            ["product_variations.variation_id", "product_variations.cafe_id"],
            name="fk_recipe_lines_variation_cafe",
        ),
        sa.PrimaryKeyConstraint("recipe_line_id"),
        sa.UniqueConstraint(
            "cafe_id",
            "variation_id",
            "effective_from",
            "ingredient_id",
            name="uq_recipe_lines_cafe_variation_effective_ingredient",
        ),
    )
    op.create_index(
        "ix_recipe_lines_cafe_variation_effective",
        "recipe_lines",
        ["cafe_id", "variation_id", sa.text("effective_from DESC")],
        unique=False,
    )

    op.create_table(
        "modifier_recipe_effects",
        sa.Column("modifier_recipe_effect_id", sa.Text(), nullable=False),
        sa.Column("cafe_id", sa.Text(), nullable=False),
        sa.Column("modifier_id", sa.Text(), nullable=False),
        sa.Column("effect", sa.Text(), nullable=False),
        sa.Column("target_ingredient_id", sa.Text(), nullable=True),
        sa.Column("ingredient_id", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=3), nullable=True),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "effect IN ('replace', 'add')",
            name="ck_modifier_recipe_effects_effect",
        ),
        sa.CheckConstraint(
            "(effect = 'replace' AND target_ingredient_id IS NOT NULL "
            "AND quantity IS NULL AND unit IS NULL) OR "
            "(effect = 'add' AND target_ingredient_id IS NULL "
            "AND quantity > 0 AND unit IN ('g', 'mL', 'each'))",
            name="ck_modifier_recipe_effects_fields",
        ),
        sa.ForeignKeyConstraint(["cafe_id"], ["cafes.cafe_id"]),
        sa.ForeignKeyConstraint(
            ["ingredient_id", "cafe_id"],
            ["inventory_items.ingredient_id", "inventory_items.cafe_id"],
            name="fk_modifier_recipe_effects_ingredient_cafe",
        ),
        sa.ForeignKeyConstraint(
            ["modifier_id", "cafe_id"],
            ["modifiers.modifier_id", "modifiers.cafe_id"],
            name="fk_modifier_recipe_effects_modifier_cafe",
        ),
        sa.ForeignKeyConstraint(
            ["target_ingredient_id", "cafe_id"],
            ["inventory_items.ingredient_id", "inventory_items.cafe_id"],
            name="fk_modifier_recipe_effects_target_ingredient_cafe",
        ),
        sa.PrimaryKeyConstraint("modifier_recipe_effect_id"),
        sa.UniqueConstraint(
            "cafe_id",
            "modifier_id",
            "effect",
            "target_ingredient_id",
            "ingredient_id",
            name="uq_modifier_recipe_effects_identity",
        ),
    )
    op.create_index(
        "ix_modifier_recipe_effects_cafe_modifier",
        "modifier_recipe_effects",
        ["cafe_id", "modifier_id"],
        unique=False,
    )
    op.create_index(
        "uq_modifier_recipe_effects_add",
        "modifier_recipe_effects",
        ["cafe_id", "modifier_id", "ingredient_id"],
        unique=True,
        postgresql_where=sa.text("effect = 'add'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_modifier_recipe_effects_add",
        table_name="modifier_recipe_effects",
    )
    op.drop_index(
        "ix_modifier_recipe_effects_cafe_modifier",
        table_name="modifier_recipe_effects",
    )
    op.drop_table("modifier_recipe_effects")
    op.drop_index(
        "ix_recipe_lines_cafe_variation_effective",
        table_name="recipe_lines",
    )
    op.drop_table("recipe_lines")

    op.drop_constraint(
        "fk_sale_items_variation_product_cafe",
        "sale_items",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_product_variations_variation_product_cafe",
        "product_variations",
        type_="unique",
    )
    op.create_foreign_key(
        "fk_sale_items_variation_cafe",
        "sale_items",
        "product_variations",
        ["variation_id", "cafe_id"],
        ["variation_id", "cafe_id"],
    )
