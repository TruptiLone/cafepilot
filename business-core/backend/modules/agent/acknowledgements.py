"""What the owner is told after a decision, written by code from what actually happened.

Every change has an honest lifecycle:
  supplier order: Proposed → Approved → Sent → Confirmed | Rejected
  shift:          Proposed → Approved → Offered → Accepted | Declined
  anything else:  Proposed → Approved → Done
"Approved" never means "done": the supplier or the employee still has to answer.

Each finished change also gets an acknowledgement (headline, details, honest caveat), and the run
gets Muse's closing line once every change has been decided. Staffing is re-measured from the
schedule as it is now.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.cafe import Cafe
from backend.modules.agent.models import AgentApproval
from backend.modules.agent.summaries import RunContext, clock, day, quantity, span
from backend.modules.purchasing.models import PurchaseOrder, ShiftOffer
from backend.modules.purchasing.service import offer_for, order_for
from backend.modules.staffing.service import StaffingService


class Acknowledgement(BaseModel):
    headline: str
    details: list[str]
    caveat: str | None = None
    tone: Literal["done", "waiting", "problem", "declined"] = "done"
    next_step: str | None = None


class Stage(BaseModel):
    stage: str
    state: Literal["done", "current", "failed", "pending"]
    at: datetime | None = None
    note: str | None = None


class ChangeStatus(BaseModel):
    lifecycle: list[Stage]
    external_status: str | None = None
    waiting_on: str | None = None
    acknowledgement: Acknowledgement | None = None


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _first(name: str) -> str:
    return name.split()[0] if name else name


def _staffing_lines(session: Session, run: Any, ctx: RunContext, pending: bool) -> list[str]:
    """Re-run every coverage check in the plan against the schedule as it is now."""
    lines: list[str] = []
    for node in run.nodes:
        if node.action != "coverage_report" or node.status != "done":
            continue
        args = node.resolved_args or node.args
        try:
            report = StaffingService(session).coverage_report(
                run.cafe_id,
                datetime.fromisoformat(args["start_at"]),
                datetime.fromisoformat(args["end_at"]),
                int(args["required_staff"]),
            )
        except Exception:
            continue
        if not report.hours:
            continue
        groups: list[list[Any]] = []
        for hour in report.hours:
            start = hour.hour_start.astimezone(ctx.tz)
            if groups and groups[-1][2] == hour.scheduled_employees:
                groups[-1][1] = start
            else:
                groups.append([start, start, hour.scheduled_employees])
        required = report.required_staff
        first = report.hours[0].hour_start.astimezone(ctx.tz)
        parts = [f"{clock(a)}–{clock(b + timedelta(hours=1))}: {n} of {required}" for a, b, n in groups]
        full = all(h.scheduled_employees >= required for h in report.hours)
        label = f"{day(first)} staffing {'once offers are accepted' if pending else 'now'}"
        lines.append(f"{label}: " + ", ".join(parts) + ("" if full else " (still short in some hours)"))
    return lines


def _approved_at(session: Session, node: Any) -> datetime | None:
    return session.scalar(
        select(AgentApproval.decided_at).where(
            AgentApproval.run_id == node.run_id, AgentApproval.node_id == node.node_id
        )
    )


def _base_stages(session: Session, node: Any) -> list[Stage]:
    if node.status == "awaiting_approval":
        return [Stage(stage="Proposed", state="current")]
    proposed = Stage(stage="Proposed", state="done", at=node.started_at)
    if node.status == "rejected":
        return [proposed, Stage(stage="Declined by you", state="failed", at=node.finished_at)]
    if node.status == "skipped":
        return [Stage(stage="Not needed", state="done", note=node.error)]
    approved = Stage(stage="Approved", state="done", at=_approved_at(session, node))
    if node.status in ("approved", "running"):
        approved.state = "current"
    return [proposed, approved]


def _order_status(session: Session, node: Any, ctx: RunContext) -> ChangeStatus:
    stages = _base_stages(session, node)
    if node.status == "failed":
        return ChangeStatus(
            lifecycle=[*stages, Stage(stage="Could not send", state="failed", note=node.error)],
            acknowledgement=Acknowledgement(
                headline="The order could not be sent", details=[node.error or ""], tone="problem"
            ),
        )
    if node.status != "done":
        return ChangeStatus(lifecycle=stages)
    order: PurchaseOrder | None = order_for(session, node.run_id, node.node_id)
    if order is None and isinstance(node.result, dict):
        order = session.get(PurchaseOrder, node.result.get("order_id"))
    if order is None:
        return ChangeStatus(lifecycle=[*stages, Stage(stage="Done", state="done")])

    supplier = order.supplier_name + (" (simulated)" if order.simulated else "")
    items = [
        f"{quantity(l.quantity, l.unit)} {ctx.ingredient(l.ingredient_id).lower()}"
        + (
            f" ({l.packs} × {l.pack_name}, {_money(l.packs * l.pack_price_cents)})"
            if l.packs and l.pack_price_cents
            else ""
        )
        for l in order.lines
    ]
    when = order.requested_delivery_at.astimezone(ctx.tz)
    details = [f"Order {order.po_number or order.order_id}", *items, f"Total {_money(order.total_cents)}"]
    sent = Stage(stage=f"Sent to {order.supplier_name}", state="done", at=order.sent_at)
    simulated_note = "Simulated supplier: no real order was sent. " if order.simulated else ""

    if order.status == "sent":
        return ChangeStatus(
            lifecycle=[*stages, sent, Stage(stage="Waiting for confirmation", state="current")],
            external_status="sent",
            waiting_on=order.supplier_name,
            acknowledgement=Acknowledgement(
                headline=f"Order sent to {supplier}",
                details=[*details, f"Requested delivery {day(when)} by {clock(when)}"],
                caveat=f"{simulated_note}Waiting for {order.supplier_name} to confirm.",
                tone="waiting",
            ),
        )
    if order.status == "rejected":
        return ChangeStatus(
            lifecycle=[
                *stages,
                sent,
                Stage(stage="Rejected by supplier", state="failed", at=order.responded_at, note=order.response_reason),
            ],
            external_status="rejected",
            acknowledgement=Acknowledgement(
                headline=f"{order.supplier_name} can't take this order",
                details=[order.response_reason or "No reason given.", "The milk shortfall is still open."],
                caveat=simulated_note.strip() or None,
                tone="problem",
                next_step="find_alternative",
            ),
        )
    confirmed_at = (order.confirmed_delivery_at or order.requested_delivery_at).astimezone(ctx.tz)
    return ChangeStatus(
        lifecycle=[*stages, sent, Stage(stage="Confirmed", state="done", at=order.responded_at)],
        external_status=order.status,
        acknowledgement=Acknowledgement(
            headline=f"{order.supplier_name} confirmed the order",
            details=[*details, f"Arriving {day(confirmed_at)} by {clock(confirmed_at)}"],
            caveat=f"{simulated_note}Stock goes up only when the delivery is received and checked in.",
            tone="done",
        ),
    )


def _shift_status(session: Session, node: Any, ctx: RunContext, staffing: list[str]) -> ChangeStatus:
    stages = _base_stages(session, node)
    if node.status == "failed":
        return ChangeStatus(
            lifecycle=[*stages, Stage(stage="Could not schedule", state="failed", note=node.error)],
            acknowledgement=Acknowledgement(
                headline="The shift could not be added", details=[node.error or ""], tone="problem"
            ),
        )
    if node.status != "done":
        return ChangeStatus(lifecycle=stages)
    result = node.result or {}
    who = result.get("employee_name") or ctx.person(str(result.get("employee_id", "")))
    when = span(result.get("start_at"), result.get("end_at"), ctx.tz)
    offer: ShiftOffer | None = offer_for(session, str(result.get("shift_id", "")))
    if offer is None:  # scheduled outside the offer flow
        return ChangeStatus(
            lifecycle=[*stages, Stage(stage="On the schedule", state="done")],
            acknowledgement=Acknowledgement(headline=f"{who} is on the schedule", details=[when, *staffing]),
        )
    offered = Stage(stage=f"Offered to {_first(who)}", state="done", at=offer.offered_at)
    note = " (simulated reply)" if offer.simulated else ""
    if offer.status == "pending":
        return ChangeStatus(
            lifecycle=[*stages, offered, Stage(stage=f"Waiting for {_first(who)}", state="current")],
            external_status="offered",
            waiting_on=_first(who),
            acknowledgement=Acknowledgement(
                headline=f"Shift offered to {who}",
                details=[when, *staffing],
                caveat=f"Waiting for {_first(who)} to reply{note}. It's on the schedule until then.",
                tone="waiting",
            ),
        )
    if offer.status == "declined":
        return ChangeStatus(
            lifecycle=[*stages, offered, Stage(stage="Declined", state="failed", at=offer.responded_at)],
            external_status="declined",
            acknowledgement=Acknowledgement(
                headline=f"{who} declined the shift",
                details=[when, "Taken off the schedule.", *staffing],
                caveat=f"Reply{note}.",
                tone="problem",
                next_step="find_cover",
            ),
        )
    return ChangeStatus(
        lifecycle=[*stages, offered, Stage(stage="Accepted", state="done", at=offer.responded_at)],
        external_status="accepted",
        acknowledgement=Acknowledgement(
            headline=f"{who} accepted: {_first(who)} will be in",
            details=[when, *staffing],
            caveat=f"Accepted{note}.",
            tone="done",
        ),
    )


def change_statuses(session: Session, run: Any) -> tuple[dict[str, ChangeStatus], str | None, list[str]]:
    """Lifecycle and acknowledgement for every change, Muse's closing line, and who we're waiting on."""
    cafe = session.get(Cafe, run.cafe_id)
    ctx = RunContext(list(run.nodes), run.cafe_id, cafe.timezone if cafe else "UTC")
    writes = [n for n in run.nodes if n.side_effect == "write"]

    pending_offers = False
    for node in writes:
        if node.action == "schedule_shift" and node.status == "done" and isinstance(node.result, dict):
            offer = offer_for(session, str(node.result.get("shift_id", "")))
            pending_offers = pending_offers or (offer is not None and offer.status == "pending")
    any_shift = any(n.action == "schedule_shift" and n.status == "done" for n in writes)
    staffing = _staffing_lines(session, run, ctx, pending_offers) if any_shift else []

    statuses: dict[str, ChangeStatus] = {}
    for node in writes:
        try:
            if node.status == "done" and node.replay_of:
                statuses[node.node_id] = ChangeStatus(
                    lifecycle=[Stage(stage="Already done in an earlier plan", state="done")],
                    acknowledgement=Acknowledgement(
                        headline="Already done in an earlier plan, not repeated", details=[node.replay_of]
                    ),
                )
            elif node.action == "propose_supplier_order":
                statuses[node.node_id] = _order_status(session, node, ctx)
            elif node.action == "schedule_shift":
                statuses[node.node_id] = _shift_status(session, node, ctx, staffing)
            else:
                stages = _base_stages(session, node)
                if node.status == "done":
                    stages.append(Stage(stage="Done", state="done", at=node.finished_at))
                statuses[node.node_id] = ChangeStatus(lifecycle=stages)
            if node.status == "rejected" and statuses[node.node_id].acknowledgement is None:
                statuses[node.node_id].acknowledgement = Acknowledgement(
                    headline="Not done: you declined this change", details=[], tone="declined"
                )
        except Exception:  # status must never break reading a run
            continue

    waiting = [s.waiting_on for s in statuses.values() if s.waiting_on]
    closing = None
    if writes and all(n.status in ("done", "rejected", "failed", "skipped") for n in writes):
        parts = [
            statuses[n.node_id].acknowledgement.headline
            for n in writes
            if n.status == "done" and n.node_id in statuses and statuses[n.node_id].acknowledgement
        ]
        declined = [n for n in writes if n.status == "rejected"]
        closing = ("Here's where things stand: " + "; ".join(parts)) if parts else "Nothing was changed"
        closing += "" if closing.endswith(".") else "."
        if staffing:
            closing += " " + staffing[0] + "."
        if declined:
            closing += f" You declined {len(declined)} change{'s' if len(declined) > 1 else ''}."
        if waiting:
            closing += f" Waiting on {' and '.join(waiting)}"
            closing += "" if closing.endswith(".") else "."
        if any(s.acknowledgement and s.acknowledgement.next_step for s in statuses.values()):
            closing += " Something needs another plan."
    return statuses, closing, waiting
