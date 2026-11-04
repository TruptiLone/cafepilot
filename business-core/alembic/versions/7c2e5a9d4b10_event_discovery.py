"""event discovery (demo): cafe_events.discovery_pending and discovered_at

Revision ID: 7c2e5a9d4b10
Revises: d054261bf4c1
Create Date: 2026-11-04 09:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c2e5a9d4b10"
down_revision: Union[str, Sequence[str], None] = "d054261bf4c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cafe_events", sa.Column("discovery_pending", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("cafe_events", sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("cafe_events", "discovered_at")
    op.drop_column("cafe_events", "discovery_pending")
