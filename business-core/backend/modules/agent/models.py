"""Persistent state for agent runs: one run per plan, one row per step, one per decision.

Nothing waits in memory: a run pauses by saving ``awaiting_approval`` and resumes when
an approval arrives. Results are kept for the owner's "Why" panel and the audit trail.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.base import Base

RUN_STATUSES = ("running", "awaiting_approval", "completed", "completed_with_issues")
NODE_STATUSES = (
    "pending",
    "awaiting_approval",
    "approved",
    "running",
    "done",
    "failed",
    "rejected",
    "skipped",
)
DECISIONS = ("approved", "rejected")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class AgentPlanRun(Base):
    __tablename__ = "agent_plan_runs"
    __table_args__ = (
        CheckConstraint(_in("status", RUN_STATUSES), name="ck_agent_plan_runs_status"),
        Index("ix_agent_plan_runs_cafe_created", "cafe_id", "created_at"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    cafe_id: Mapped[str] = mapped_column(Text, ForeignKey("cafes.cafe_id"), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    assumptions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    edges: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    levels: Mapped[list[list[str]]] = mapped_column(JSONB, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    parent_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_node_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    nodes: Mapped[list["AgentNodeRun"]] = relationship(
        back_populates="run",
        order_by="(AgentNodeRun.level, AgentNodeRun.node_id)",
        cascade="all, delete-orphan",
    )


class AgentNodeRun(Base):
    __tablename__ = "agent_node_runs"
    __table_args__ = (
        CheckConstraint(_in("status", NODE_STATUSES), name="ck_agent_node_runs_status"),
        CheckConstraint("side_effect IN ('read', 'write')", name="ck_agent_node_runs_side_effect"),
        Index("ix_agent_node_runs_idempotency", "idempotency_key", "status"),
    )

    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("agent_plan_runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    node_id: Mapped[str] = mapped_column(Text, primary_key=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    side_effect: Mapped[str] = mapped_column(Text, nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    only_if: Mapped[str | None] = mapped_column(Text, nullable=True)
    args: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    resolved_args: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    depends_on: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    replay_of: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[AgentPlanRun] = relationship(back_populates="nodes")


class AgentApproval(Base):
    __tablename__ = "agent_approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "node_id"],
            ["agent_node_runs.run_id", "agent_node_runs.node_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("decision", DECISIONS), name="ck_agent_approvals_decision"),
        Index("ix_agent_approvals_run_node", "run_id", "node_id"),
    )

    approval_id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    node_id: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_args: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    edited_args: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
