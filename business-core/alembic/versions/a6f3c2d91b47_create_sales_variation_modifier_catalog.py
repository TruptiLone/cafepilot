"""create sales variation and modifier catalog

Revision ID: a6f3c2d91b47
Revises: 3411712f6af3
Create Date: 2026-10-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a6f3c2d91b47"
down_revision: Union[str, Sequence[str], None] = "3411712f6af3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_variations",
        sa.Column("variation_id", sa.Text(), nullable=False),
        sa.Column("cafe_id", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("size_oz", sa.Integer(), nullable=True),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "code IN ('REG', 'LRG')",
            name="ck_product_variations_code",
        ),
        sa.CheckConstraint(
            "price_cents >= 0",
            name="ck_product_variations_price_nonnegative",
        ),
        sa.CheckConstraint(
            "size_oz IS NULL OR size_oz > 0",
            name="ck_product_variations_size_positive",
        ),
        sa.ForeignKeyConstraint(["cafe_id"], ["cafes.cafe_id"]),
        sa.ForeignKeyConstraint(
            ["product_id", "cafe_id"],
            ["menu_products.product_id", "menu_products.cafe_id"],
            name="fk_product_variations_product_cafe",
        ),
        sa.PrimaryKeyConstraint("variation_id"),
        sa.UniqueConstraint(
            "cafe_id",
            "product_id",
            "code",
            name="uq_product_variations_cafe_product_code",
        ),
        sa.UniqueConstraint(
            "variation_id",
            "cafe_id",
            name="uq_product_variations_variation_cafe",
        ),
    )
    op.create_index(
        "ix_product_variations_cafe_id_product_id_active",
        "product_variations",
        ["cafe_id", "product_id", "active"],
        unique=False,
    )

    op.create_table(
        "modifier_groups",
        sa.Column("group_id", sa.Text(), nullable=False),
        sa.Column("cafe_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("selection_type", sa.Text(), nullable=False),
        sa.Column(
            "min_select",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("max_select", sa.Integer(), nullable=False),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "max_select >= 1",
            name="ck_modifier_groups_max_select_positive",
        ),
        sa.CheckConstraint(
            "max_select >= min_select",
            name="ck_modifier_groups_selection_range",
        ),
        sa.CheckConstraint(
            "min_select >= 0",
            name="ck_modifier_groups_min_select_nonnegative",
        ),
        sa.CheckConstraint(
            "selection_type IN ('single', 'multiple')",
            name="ck_modifier_groups_selection_type",
        ),
        sa.ForeignKeyConstraint(["cafe_id"], ["cafes.cafe_id"]),
        sa.PrimaryKeyConstraint("group_id"),
        sa.UniqueConstraint(
            "cafe_id",
            "name",
            name="uq_modifier_groups_cafe_name",
        ),
        sa.UniqueConstraint(
            "group_id",
            "cafe_id",
            name="uq_modifier_groups_group_cafe",
        ),
    )
    op.create_index(
        "ix_modifier_groups_cafe_id_active",
        "modifier_groups",
        ["cafe_id", "active"],
        unique=False,
    )

    op.create_table(
        "modifiers",
        sa.Column("modifier_id", sa.Text(), nullable=False),
        sa.Column("cafe_id", sa.Text(), nullable=False),
        sa.Column("group_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column(
            "is_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "price_cents >= 0",
            name="ck_modifiers_price_nonnegative",
        ),
        sa.ForeignKeyConstraint(["cafe_id"], ["cafes.cafe_id"]),
        sa.ForeignKeyConstraint(
            ["group_id", "cafe_id"],
            ["modifier_groups.group_id", "modifier_groups.cafe_id"],
            name="fk_modifiers_group_cafe",
        ),
        sa.PrimaryKeyConstraint("modifier_id"),
        sa.UniqueConstraint(
            "cafe_id",
            "group_id",
            "name",
            name="uq_modifiers_cafe_group_name",
        ),
        sa.UniqueConstraint(
            "modifier_id",
            "cafe_id",
            name="uq_modifiers_modifier_cafe",
        ),
    )
    op.create_index(
        "ix_modifiers_cafe_id_group_id_active",
        "modifiers",
        ["cafe_id", "group_id", "active"],
        unique=False,
    )
    op.create_index(
        "uq_modifiers_cafe_group_default",
        "modifiers",
        ["cafe_id", "group_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    op.create_table(
        "product_modifier_groups",
        sa.Column("product_modifier_group_id", sa.Text(), nullable=False),
        sa.Column("cafe_id", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Text(), nullable=False),
        sa.Column("group_id", sa.Text(), nullable=False),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["cafe_id"], ["cafes.cafe_id"]),
        sa.ForeignKeyConstraint(
            ["group_id", "cafe_id"],
            ["modifier_groups.group_id", "modifier_groups.cafe_id"],
            name="fk_product_modifier_groups_group_cafe",
        ),
        sa.ForeignKeyConstraint(
            ["product_id", "cafe_id"],
            ["menu_products.product_id", "menu_products.cafe_id"],
            name="fk_product_modifier_groups_product_cafe",
        ),
        sa.PrimaryKeyConstraint("product_modifier_group_id"),
        sa.UniqueConstraint(
            "cafe_id",
            "product_id",
            "group_id",
            name="uq_product_modifier_groups_cafe_product_group",
        ),
    )
    op.create_index(
        "ix_product_modifier_groups_cafe_id_group_id",
        "product_modifier_groups",
        ["cafe_id", "group_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_modifier_groups_cafe_id_group_id",
        table_name="product_modifier_groups",
    )
    op.drop_table("product_modifier_groups")
    op.drop_index("uq_modifiers_cafe_group_default", table_name="modifiers")
    op.drop_index(
        "ix_modifiers_cafe_id_group_id_active",
        table_name="modifiers",
    )
    op.drop_table("modifiers")
    op.drop_index(
        "ix_modifier_groups_cafe_id_active",
        table_name="modifier_groups",
    )
    op.drop_table("modifier_groups")
    op.drop_index(
        "ix_product_variations_cafe_id_product_id_active",
        table_name="product_variations",
    )
    op.drop_table("product_variations")
