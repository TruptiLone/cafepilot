"""When a supplier rejects an order or an employee declines a shift, Muse plans again.

The new request is written by code from what actually went wrong (who said no, why, what is
still needed and by when), so the model starts from facts, not from a summary it wrote itself.
The new run is linked to the change that failed.
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.cafe import Cafe
from backend.modules.agent.models import AgentPlanRun
from backend.modules.agent.summaries import RunContext, clock, day, quantity, span
from backend.modules.purchasing.models import PurchaseOrder
from backend.modules.purchasing.service import offer_for, order_for, settle_due


class NothingToReplanError(ValueError):
    """The change did not fail, so there is nothing to replan."""


def replan_request(session: Session, run: AgentPlanRun, node_id: str) -> str:
    settle_due(session, cafe_id=run.cafe_id)
    node = next((n for n in run.nodes if n.node_id == node_id), None)
    if node is None:
        raise LookupError(node_id)
    cafe = session.get(Cafe, run.cafe_id)
    ctx = RunContext(list(run.nodes), run.cafe_id, cafe.timezone if cafe else "UTC")
    tz = ZoneInfo(cafe.timezone if cafe else "UTC")

    if node.action == "propose_supplier_order" and node.status == "done":
        order: PurchaseOrder | None = order_for(session, run.run_id, node_id)
        if order is None or order.status != "rejected":
            raise NothingToReplanError("this order was not rejected")
        when = order.requested_delivery_at.astimezone(tz)
        items = ", ".join(
            f"{quantity(l.quantity, l.unit)} {ctx.ingredient(l.ingredient_id).lower()}" for l in order.lines
        )
        return (
            f"{order.supplier_name} rejected purchase order {order.order_id}: {order.response_reason} "
            f"We still need {items} delivered by {day(when)} {clock(when)} "
            f"({order.requested_delivery_at.astimezone(tz).isoformat()}). Find another way to get the same "
            f"quantities in time (use get_purchase_order for {order.order_id} and its reorder_lines; pick a "
            f"supplier that can deliver by then). Do not change staffing."
        )

    if node.action == "schedule_shift" and node.status == "done" and isinstance(node.result, dict):
        offer = offer_for(session, str(node.result.get("shift_id", "")))
        if offer is None or offer.status != "declined":
            raise NothingToReplanError("this shift was not declined")
        who = node.result.get("employee_name") or ctx.person(str(node.result.get("employee_id", "")))
        when = span(node.result.get("start_at"), node.result.get("end_at"), tz)
        required = next(
            (
                (n.resolved_args or n.args).get("required_staff")
                for n in run.nodes
                if n.action == "coverage_report" and n.status == "done"
            ),
            None,
        )
        target = f" Our staffing target for those hours is {required} people." if required else ""
        return (
            f"{who} ({node.result.get('employee_id')}) declined the shift {when} "
            f"({node.result.get('start_at')} to {node.result.get('end_at')}).{target} Find someone else "
            f"who can cover those hours, or tell me nobody is available. Do not order anything."
        )
    raise NothingToReplanError("only rejected orders and declined shifts can be replanned")


def replans_of(session: Session, run_id: str) -> dict[str, str]:
    """node_id -> the newest run that replanned it."""
    rows = session.execute(
        select(AgentPlanRun.parent_node_id, AgentPlanRun.run_id)
        .where(AgentPlanRun.parent_run_id == run_id)
        .order_by(AgentPlanRun.created_at)
    )
    return {node_id: child for node_id, child in rows if node_id}
