from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from backend.db.base import Base
from backend.db.config import get_database_connect_args, get_settings
from backend.models.cafe import Cafe
from backend.modules.agent.models import AgentApproval, AgentNodeRun, AgentPlanRun
from backend.modules.events.models import CafeEvent  # noqa: F401
from backend.modules.purchasing.models import PurchaseOrder, PurchaseOrderLine, ShiftOffer  # noqa: F401
from backend.modules.inventory.models import (
    InventoryItem,
    InventoryMovement,
    StockCount,
    StockCountLine,
)
from backend.modules.sales.models import (
    MenuProduct,
    Modifier,
    ModifierGroup,
    ModifierRecipeEffect,
    ProductModifierGroup,
    ProductVariation,
    RecipeLine,
    Sale,
    SaleItem,
    SaleItemModifier,
)
from backend.modules.staffing.models import Employee, Shift

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        dialect_name="postgresql",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        get_settings().database_url,
        connect_args=get_database_connect_args(),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
