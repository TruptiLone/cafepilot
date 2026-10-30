"""One plain-language line per plan step, written by code from the step's real result.

The UI shows these on each tile so a viewer understands every step without clicking and
without the frontend doing any arithmetic. Nothing here calls the model.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from backend.modules.agent.cafe_policies import policies_for

Row = dict[str, Any]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def quantity(value: Any, unit: str | None) -> str:
    """9600 mL -> '9.6 L', 1080 g -> '1.08 kg', 30 each -> '30'."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return f"{value} {unit or ''}".strip()
    unit = unit or ""
    if unit == "mL" and abs(number) >= 1000:
        number, unit = number / 1000, "L"
    elif unit == "g" and abs(number) >= 1000:
        number, unit = number / 1000, "kg"
    text = f"{number.normalize():f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text if unit in ("", "each") else f"{text} {unit}"


def _local(value: Any, tz: ZoneInfo) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(tz)
    except ValueError:
        return None


def clock(moment: datetime) -> str:
    text = moment.strftime("%I:%M %p").lstrip("0")
    return text.replace(":00 ", " ")


def day(moment: datetime) -> str:
    return f"{moment.strftime('%a %b')} {moment.day}"


def span(start: Any, end: Any, tz: ZoneInfo, *, with_day: bool = True) -> str:
    a, b = _local(start, tz), _local(end, tz)
    if a is None or b is None:
        return ""
    times = f"{clock(a)}–{clock(b)}"
    return f"{day(a)}, {times}" if with_day else times


def _list(parts: Iterable[str], limit: int = 3) -> str:
    items = [p for p in parts if p]
    if len(items) > limit:
        return ", ".join(items[:limit]) + f" and {len(items) - limit} more"
    return ", ".join(items)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


# ---------------------------------------------------------------------------
# Context gathered from the whole run (names for ids)
# ---------------------------------------------------------------------------


class RunContext:
    def __init__(self, nodes: Iterable[Any], cafe_id: str, timezone: str) -> None:
        self.tz = ZoneInfo(timezone)
        self.cafe_id = cafe_id
        self.ingredients: dict[str, Row] = {}
        self.employees: dict[str, Row] = {}
        for node in nodes:
            if node.status != "done" or node.result is None:
                continue
            rows: list[Row] = []
            result = node.result
            if isinstance(result, list):
                rows = [r for r in result if isinstance(r, dict)]
            elif isinstance(result, dict):
                rows = [result]
                for key in ("lines", "ingredients"):
                    rows += [r for r in result.get(key) or [] if isinstance(r, dict)]
            for row in rows:
                if row.get("ingredient_id") and row.get("name"):
                    self.ingredients.setdefault(row["ingredient_id"], row)
                if row.get("employee_id") and (row.get("name") or row.get("employee_name")):
                    self.employees.setdefault(row["employee_id"], row)

    def ingredient(self, ingredient_id: str) -> str:
        row = self.ingredients.get(ingredient_id)
        if row:
            return str(row["name"])
        return ingredient_id.replace("_", " ").lower()

    def person(self, employee_id: str) -> str:
        row = self.employees.get(employee_id)
        if row:
            return str(row.get("name") or row.get("employee_name"))
        return employee_id


# ---------------------------------------------------------------------------
# Per-action summaries of a finished result
# ---------------------------------------------------------------------------


def _stock(result: list[Row], ctx: RunContext) -> str:
    known = [r for r in result if r.get("stock_status") == "known"]
    unsure = [r for r in result if r.get("stock_status") != "known"]
    if len(result) <= 4:
        parts = [
            f"{r['name']} {quantity(r['estimated_quantity'], r.get('base_unit'))}"
            if r.get("stock_status") == "known"
            else f"{r['name']} unknown"
            for r in result
        ]
        return _list(parts, 4) + " on hand"
    text = f"{_plural(len(known), 'item')} known"
    if unsure:
        text += f"; not sure about {_list(r['name'] for r in unsure)}"
    return text


def _projection(result: Row, ctx: RunContext) -> str:
    parts = [f"{quantity(i['quantity'], i.get('unit'))} {i['name'].lower()}" for i in result.get("ingredients", [])]
    text = "Need " + _list(parts, 4) if parts else "No ingredients needed"
    if result.get("unmapped_variations"):
        text += f"; no recipe for {_list(result['unmapped_variations'])}"
    return text


def _shortfall(result: Row, ctx: RunContext) -> str:
    lines = result.get("lines", [])
    short = [l for l in lines if l.get("status") == "short"]
    enough = [l for l in lines if l.get("status") == "enough"]
    unknown = [l for l in lines if l.get("status") == "unknown_stock"]
    parts = []
    def ordered(l: Row) -> str:
        text = f"{quantity(l['order_quantity'], l['unit'])} {l['name'].lower()}"
        if l.get("packs") and l.get("pack_name"):
            text += f" ({l['packs']} × {l['pack_name']})"
        return text

    if short:
        parts.append("Order " + _list(ordered(l) for l in short))
    else:
        parts.append("Nothing to order")
    if enough and len(enough) <= 2:
        parts.append(f"{_list(l['name'].lower() for l in enough)} enough")
    elif enough:
        parts.append("everything else is covered")
    if unknown:
        parts.append(f"count {_list(l['name'].lower() for l in unknown)} first")
    basis = f"includes {quantity(result.get('buffer_pct'), '')}% buffer"
    if result.get("include_normal_use"):
        basis = "after normal use and regular deliveries; " + basis
    return "; ".join(parts) + f" ({basis})"


def _demand(result: Row, ctx: RunContext) -> str:
    d, food = result.get("drinks", {}), result.get("food", {})
    n = len(result.get("examples", []))
    if result.get("confidence") == "none":
        return f"No past events like this; normal day is {d.get('normal', 0)} drinks"
    low, typ, high = d.get("extra_low", 0), d.get("extra_typical", 0), d.get("extra_high", 0)
    sign = lambda v: f"+{v}" if v > 0 else str(v)  # noqa: E731
    rng = sign(typ) if low == high else f"{sign(low)} to {sign(high)} (typically {sign(typ)})"
    names = {e.get("name") for e in result.get("examples", [])}
    if len(names) == 1:
        name = names.pop()
        who = f"Last {name}" if n == 1 else f"Last {n} {name}s"
    else:
        who = f"{n} past event{'s' if n != 1 else ''}"
    text = f"{who}: {rng} drinks on a normal {d.get('normal', 0)}"
    if food.get("extra_typical"):
        text += f"; pastries {sign(food['extra_typical'])}"
    if result.get("confidence") == "rough":
        text += " (rough guide)"
    return text


def _coverage(result: Row, ctx: RunContext) -> str | None:
    hours = result.get("hours", [])
    if not hours:
        return "No hours in range"
    required = hours[0].get("required_staff")
    if all(h.get("coverage_status") == "adequate" for h in hours):
        return f"Fully staffed: at least {required} people every hour"
    # group consecutive hours with the same headcount
    groups: list[tuple[datetime, datetime, int]] = []
    for hour in hours:
        start = _local(hour["hour_start"], ctx.tz)
        if start is None:
            continue
        count = int(hour["scheduled_employees"])
        end = start + timedelta(hours=1)
        if groups and groups[-1][2] == count and groups[-1][1] == start:
            groups[-1] = (groups[-1][0], end, count)
        else:
            groups.append((start, end, count))
    parts = [f"{clock(a)}–{clock(b)} has {n} of {required}" for a, b, n in groups if n < (required or 0)]
    if not parts:
        return None
    first = _local(hours[0]["hour_start"], ctx.tz)
    prefix = f"{day(first)}: " if first else ""
    return f"Short-staffed {prefix}" + ", ".join(parts) + " people"


def _employees(result: list[Row], ctx: RunContext) -> str:
    active = [e for e in result if e.get("active", True)]
    roles = policies_for(ctx.cafe_id).get("staffing", {}).get("espresso_roles", [])
    espresso = [e for e in active if e.get("role") in roles]
    text = f"{_plural(len(active), 'active staff member')}"
    if roles:
        text += f", {len(espresso)} can make espresso"
    return text


def _shifts(result: list[Row], ctx: RunContext) -> str:
    scheduled = [s for s in result if s.get("status", "scheduled") == "scheduled"]
    if not scheduled:
        return "No shifts scheduled in this window"
    parts = [
        f"{str(s.get('employee_name') or ctx.person(s['employee_id'])).split()[0]} "
        f"{span(s['start_at'], s['end_at'], ctx.tz, with_day=False)}"
        for s in scheduled
    ]
    return f"{_plural(len(scheduled), 'shift')}: {_list(parts, 4)}"


def _shift_change(args: Row, ctx: RunContext, done: bool) -> str:
    who = ctx.person(str(args.get("employee_id", "")))
    when = span(args.get("start_at"), args.get("end_at"), ctx.tz)
    return f"{'Scheduled' if done else 'Schedule'} {who}, {when}"


def _order(args: Row, result: Row | None, ctx: RunContext, done: bool) -> str:
    lines = (result or {}).get("lines") or args.get("lines") or []
    if isinstance(lines, str):
        return "Order the shortfall from the simulated supplier"
    items = _list(f"{quantity(l['quantity'], l.get('unit'))} {ctx.ingredient(l['ingredient_id']).lower()}" for l in lines)
    delivery = _local((result or {}).get("confirmed_delivery_at") or args.get("requested_delivery_at"), ctx.tz)
    when = f" for {day(delivery)} {clock(delivery)}" if delivery else ""
    if done:
        ref = (result or {}).get("order_ref", "")
        return f"Simulated supplier confirmed {items}{when}" + (f" ({ref})" if ref else "")
    return f"Order {items}{when} (simulated supplier)"


def _sales_summary(result: Row, ctx: RunContext) -> str:
    sales = result.get("sale_count") or result.get("sales_count") or result.get("total_sales")
    revenue = result.get("revenue") or result.get("gross_sales") or result.get("total_revenue")
    parts = []
    if sales is not None:
        parts.append(f"{sales} sales")
    if revenue is not None:
        parts.append(f"${quantity(revenue, '')} revenue")
    return ", ".join(parts) or "Sales summarized"


def summarize_step(node: Any, ctx: RunContext) -> str | None:
    """A short line for the tile. Uses the result when done, the arguments when waiting."""
    action, status = node.action, node.status
    args: Row = node.resolved_args or node.args or {}
    result = node.result

    if status == "skipped":
        return node.error or "Skipped"
    if status == "failed":
        return f"Could not finish: {node.error}" if node.error else "Could not finish"
    if status == "rejected":
        base = _change_line(action, args, None, ctx, done=False)
        return f"Declined: {base}" if base else "Declined"
    if node.replay_of and status == "done":
        base = _change_line(action, args, result, ctx, done=True)
        return f"Already done earlier, not repeated: {base}" if base else "Already done earlier"
    if status in ("awaiting_approval", "approved", "running") and node.side_effect == "write":
        return _change_line(action, args, None, ctx, done=False)
    if status != "done" or result is None:
        return None

    if action == "get_stock_estimates" and isinstance(result, list):
        return _stock(result, ctx)
    if action == "project_recipe_usage" and isinstance(result, dict):
        return _projection(result, ctx)
    if action == "calculate_shortfall" and isinstance(result, dict):
        return _shortfall(result, ctx)
    if action == "estimate_event_demand" and isinstance(result, dict):
        return _demand(result, ctx)
    if action == "coverage_report" and isinstance(result, dict):
        return _coverage(result, ctx)
    if action == "list_employees" and isinstance(result, list):
        return _employees(result, ctx)
    if action == "list_shifts" and isinstance(result, list):
        return _shifts(result, ctx)
    if action == "sales_summary" and isinstance(result, dict):
        return _sales_summary(result, ctx)
    if node.side_effect == "write":
        return _change_line(action, args, result if isinstance(result, dict) else None, ctx, done=True)
    if isinstance(result, list):
        noun = {
            "list_ingredients": "ingredient",
            "list_products": "product",
            "list_sales": "sale",
            "list_stock_counts": "stock count",
            "list_inventory_movements": "stock movement",
        }.get(action, "record")
        return f"Found {_plural(len(result), noun)}"
    return None


def _change_line(action: str, args: Row, result: Row | None, ctx: RunContext, *, done: bool) -> str | None:
    if action == "schedule_shift":
        return _shift_change(args, ctx, done)
    if action == "propose_supplier_order":
        return _order(args, result, ctx, done)
    if action == "cancel_shift":
        return f"{'Cancelled' if done else 'Cancel'} shift {args.get('shift_id', '')}".strip()
    if action == "create_stock_count":
        items = _list(
            f"{ctx.ingredient(i['ingredient_id']).lower()} {i.get('counted_quantity')}" for i in args.get("items", [])
        )
        return f"{'Recorded' if done else 'Record'} a stock count: {items}"
    if action == "verify_stock_count":
        return f"{'Verified' if done else 'Verify'} stock count {args.get('count_id', '')}".strip()
    if action == "record_inventory_movement":
        verb = "Recorded" if done else "Record"
        return (
            f"{verb} {str(args.get('movement_type', 'movement')).lower()} of "
            f"{quantity(args.get('quantity_delta'), '')} {ctx.ingredient(str(args.get('ingredient_id', ''))).lower()}"
        )
    return None


def summarize_run(nodes: list[Any], cafe_id: str, timezone: str) -> dict[str, str | None]:
    ctx = RunContext(nodes, cafe_id, timezone)
    summaries: dict[str, str | None] = {}
    for node in nodes:
        try:
            summaries[node.node_id] = summarize_step(node, ctx)
        except Exception:  # a summary must never break reading a run
            summaries[node.node_id] = None
    return summaries
