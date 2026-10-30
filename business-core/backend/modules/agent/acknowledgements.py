"""What the owner is told after a decision, written by code from what actually happened.

Each finished change gets an acknowledgement (headline, details, an honest caveat), and a run
whose decisions are all made gets Muse's closing line. Shift acknowledgements re-check staff
coverage after the change, so "now 4 of 4" is measured, not assumed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.models.cafe import Cafe
from backend.modules.agent.summaries import RunContext, clock, day, quantity, span
from backend.modules.staffing.service import StaffingService


class Acknowledgement(BaseModel):
    headline: str
    details: list[str]
    caveat: str | None = None


def _coverage_now(session: Session, run: Any, ctx: RunContext) -> list[str]:
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
        groups: list[list[Any]] = []
        for hour in report.hours:
            start = hour.hour_start.astimezone(ctx.tz)
            if groups and groups[-1][2] == hour.scheduled_employees:
                groups[-1][1] = start
            else:
                groups.append([start, start, hour.scheduled_employees])
        required = report.required_staff
        first = report.hours[0].hour_start.astimezone(ctx.tz) if report.hours else None
        parts = []
        for start, last, count in groups:
            parts.append(f"{clock(start)}–{clock(last + timedelta(hours=1))}: {count} of {required}")
        prefix = f"{day(first)} staffing now" if first else "Staffing now"
        full = all(h.scheduled_employees >= required for h in report.hours)
        lines.append(f"{prefix}: " + ", ".join(parts) + ("" if full else " (still short in some hours)"))
    return lines


def _order(node: Any, ctx: RunContext) -> Acknowledgement:
    result = node.result or {}
    lines = result.get("lines", [])
    packs: dict[str, tuple[int, str]] = {}
    for other in ctx.nodes:
        if other.action == "calculate_shortfall" and other.status == "done" and isinstance(other.result, dict):
            for line in other.result.get("lines", []):
                if line.get("packs"):
                    packs[line["ingredient_id"]] = (line["packs"], line.get("pack_name") or "pack")
    details = [f"Reference {result.get('order_ref', '—')}"]
    for line in lines:
        text = f"{quantity(line['quantity'], line.get('unit'))} {ctx.ingredient(line['ingredient_id']).lower()}"
        if line["ingredient_id"] in packs:
            count, name = packs[line["ingredient_id"]]
            text += f" ({count} × {name})"
        details.append(text)
    delivery = result.get("confirmed_delivery_at")
    if delivery:
        moment = datetime.fromisoformat(delivery).astimezone(ctx.tz)
        details.append(f"Delivery {day(moment)} by {clock(moment)}")
    return Acknowledgement(
        headline="Order placed with the supplier (simulated)",
        details=details,
        caveat=(
            "Simulated supplier: no real order was sent. Stock goes up only when the delivery is received "
            "and checked in."
        )
        if result.get("simulated", True)
        else "Stock goes up only when the delivery is received and checked in.",
    )


def _shift(node: Any, ctx: RunContext, coverage: list[str]) -> Acknowledgement:
    result = node.result or {}
    who = result.get("employee_name") or ctx.person(str(result.get("employee_id", "")))
    when = span(result.get("start_at"), result.get("end_at"), ctx.tz)
    return Acknowledgement(
        headline=f"{who} is on the schedule",
        details=[when, *coverage],
        caveat=f"{who.split()[0]} hasn't been notified yet: staff messaging isn't connected.",
    )


def acknowledge_run(session: Session, run: Any) -> tuple[dict[str, Acknowledgement], str | None]:
    """Per-step acknowledgements for finished changes, and Muse's closing line when all are decided."""
    cafe = session.get(Cafe, run.cafe_id)
    ctx = RunContext(list(run.nodes), run.cafe_id, cafe.timezone if cafe else "UTC")
    ctx.nodes = list(run.nodes)  # type: ignore[attr-defined]
    writes = [n for n in run.nodes if n.side_effect == "write"]
    shifts_done = [n for n in writes if n.action == "schedule_shift" and n.status == "done"]
    coverage = _coverage_now(session, run, ctx) if shifts_done else []

    acks: dict[str, Acknowledgement] = {}
    for node in writes:
        try:
            if node.status == "done" and node.replay_of:
                acks[node.node_id] = Acknowledgement(
                    headline="Already done in an earlier plan, not repeated", details=[node.replay_of]
                )
            elif node.status == "done" and node.action == "propose_supplier_order":
                acks[node.node_id] = _order(node, ctx)
            elif node.status == "done" and node.action == "schedule_shift":
                acks[node.node_id] = _shift(node, ctx, coverage)
            elif node.status == "rejected":
                acks[node.node_id] = Acknowledgement(headline="Not done: you declined this change", details=[])
            elif node.status == "failed":
                acks[node.node_id] = Acknowledgement(
                    headline="Could not complete this change", details=[node.error or "Unknown error"]
                )
        except Exception:  # an acknowledgement must never break reading a run
            continue

    closing = None
    if writes and all(n.status in ("done", "rejected", "failed", "skipped") for n in writes):
        parts: list[str] = []
        orders = [n for n in writes if n.action == "propose_supplier_order" and n.status == "done" and n.result]
        for order in orders:
            items = ", ".join(
                f"{quantity(l['quantity'], l.get('unit'))} {ctx.ingredient(l['ingredient_id']).lower()}"
                for l in order.result.get("lines", [])
            )
            moment = datetime.fromisoformat(order.result["confirmed_delivery_at"]).astimezone(ctx.tz)
            parts.append(f"{items} arriving {day(moment)} by {clock(moment)} (simulated supplier)")
        if shifts_done:
            names = [
                (n.result or {}).get("employee_name") or ctx.person(str((n.result or {}).get("employee_id", "")))
                for n in shifts_done
            ]
            joined = " and ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + f" and {names[-1]}"
            parts.append(f"{joined} {'is' if len(names) == 1 else 'are'} on the schedule")
        declined = [n for n in writes if n.status == "rejected"]
        failed = [n for n in writes if n.status == "failed"]
        if parts:
            closing = "Done. " + "; ".join(parts) + "."
        else:
            closing = "Nothing was changed."
        if coverage:
            closing += " " + coverage[0] + "."
        if declined:
            closing += f" You declined {len(declined)} change{'s' if len(declined) > 1 else ''}."
        if failed:
            closing += f" {len(failed)} change{'s' if len(failed) > 1 else ''} could not be completed."
    return acks, closing
