"""Ask Muse a question: read-only lookups chosen by Muse Spark, answered by code.

Spark sees the owner's message, the read-only actions (with their input schemas) and reference data
worked out in code. It returns only {intent, actions}. Code validates every action with the
registry's own input schema, runs it, and writes the answer from the action's existing summary and
its rows. The model never writes a number, and this path never writes: only read actions run, and the
session is rolled back afterwards.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, time as clock_time, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from types import SimpleNamespace
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.config import ENV_FILE, get_settings
from backend.modules.agent.ai_calls import call_model, is_timeout, record as record_ai_call
from backend.modules.agent.planner import (
    ModelCallError,
    PlannerUnavailableError,
    _describe_model_error,
    _log_call,
    build_planning_context,
)
from backend.modules.agent.registry import REGISTRY
from backend.modules.agent.summaries import RunContext, clock, day, local_time, quantity, span, summarize_step
from backend.modules.inventory.models import InventoryItem
from backend.modules.purchasing.models import PurchaseOrder

TIMEOUT_SECONDS = 10
MAX_ACTIONS = 3
MAX_ROWS = 20
READ_ACTIONS = {name: action for name, action in REGISTRY.items() if action.side_effect == "read"}
NOT_ON_LIST = "That's not on our stock list."


class ChatSettings(BaseSettings):
    """The chat model, from business-core/.env (CHAT_MODEL); Muse Spark by default."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    chat_model: str = "muse-spark-1.3"


@lru_cache
def chat_settings() -> ChatSettings:
    return ChatSettings()


class ChatTimeoutError(RuntimeError):
    pass


def create_chat_client() -> Any:
    settings = get_settings()
    key = settings.model_api_key.get_secret_value().strip() if settings.model_api_key else ""
    if not key:
        raise PlannerUnavailableError("MODEL_API_KEY is not set in business-core/.env")
    from openai import OpenAI

    return OpenAI(api_key=key, base_url=settings.model_api_base_url, timeout=TIMEOUT_SECONDS, max_retries=0)


# ---------------------------------------------------------------------------
# What the model returns
# ---------------------------------------------------------------------------


class ChatAction(BaseModel):
    action: str
    args: dict[str, Any] = Field(default_factory=dict)


class ChatRouting(BaseModel):
    intent: Literal["answer", "needs_plan", "out_of_scope"]
    actions: list[ChatAction] = Field(default_factory=list, max_length=MAX_ACTIONS)


SYSTEM_PROMPT = """You route a café owner's message to read-only lookups for CaféPilot. Code runs the
lookups and writes the answer: you never answer in words and never state a number.

Return ONLY one JSON object, no prose, no code fences:
{"intent": "answer" | "needs_plan" | "out_of_scope", "actions": [{"action": "<name>", "args": {...}}]}

- answer: the owner wants to know something about this café. Pick 1 to 3 actions from READ_ACTIONS
  with args exactly per their input_schema. Use IDs only from REFERENCE (ingredient_id, employee_id,
  variation_id, event_id, order_id). Copy start/end times from REFERENCE time_windows (they carry the
  café's UTC offset); use REFERENCE now as as_of.
- Stock ("do we have", "how much", "are we low on anything"): get_stock_estimates. For one item set
  ingredient_id; for several use ingredient_ids; for "anything" or "everything" leave both out. If the
  item is not in REFERENCE ingredients, still call get_stock_estimates with ingredient_id set to the
  item in UPPER_SNAKE_CASE.
- Sales ("how many did we sell", "how was today"): sales_summary over the time window asked.
- Staff ("who's working"): list_shifts over that window.
- Deliveries ("when does the milk arrive", "what's coming"): list_deliveries with start_at = REFERENCE
  now; set ingredient_id for one item, or ingredient_ids for several. "Milk" on its own means every
  milk in REFERENCE ingredients. It covers open orders and regular deliveries, so use it even when
  REFERENCE open_orders is empty.
- needs_plan: the owner wants something changed, ordered, scheduled, cancelled or recorded. No actions.
- out_of_scope: not about running this café. No actions.
"""


REPLY_PROMPT = """You are Muse, the assistant at an independent café. Code has already looked up the
answer to the owner's question; the results are below.

Reply like a helpful café manager talking to the owner. Start with one or two plain sentences that
answer the question directly. Don't repeat the table in the sentence, and no labels like
'Below reorder point:'. Answer only what was asked: for "do we have X", say how many; don't bring up
reorder points unless the owner asked whether something is low. Don't offer to do anything and don't ask the owner a question: the buttons
under your reply handle next steps, like planning an order. Use only numbers that appear in the tool
results. Cover everything the code answer says, including items with no scheduled delivery.

Return only the reply: plain text, no markdown, at most two sentences."""


# ---------------------------------------------------------------------------
# Reference data (code)
# ---------------------------------------------------------------------------


def _window(start: datetime, end: datetime) -> dict[str, str]:
    return {"start_at": start.isoformat(), "end_at": end.isoformat()}


def time_windows(now: datetime, tz: ZoneInfo) -> dict[str, Any]:
    """Today, tomorrow, this week (Mon-Sun) and the next 7 days, in café time, from the reference time."""
    local = now.astimezone(tz)
    midnight = lambda day: datetime.combine(day, clock_time.min, tz)  # noqa: E731
    today = local.date()
    monday = today - timedelta(days=today.weekday())
    days = {}
    for offset in range(8):
        day = today + timedelta(days=offset)
        days[f"{day.strftime('%A').lower()} {day.isoformat()}"] = {
            **_window(midnight(day), midnight(day + timedelta(days=1))),
            "morning": _window(midnight(day) + timedelta(hours=6), midnight(day) + timedelta(hours=12)),
            "afternoon": _window(midnight(day) + timedelta(hours=12), midnight(day) + timedelta(hours=18)),
        }
    return {
        "today": _window(midnight(today), midnight(today + timedelta(days=1))),
        "so_far_today": _window(midnight(today), local),
        "tomorrow": _window(midnight(today + timedelta(days=1)), midnight(today + timedelta(days=2))),
        "this_week": _window(midnight(monday), midnight(monday + timedelta(days=7))),
        "days": days,
    }


def _open_orders(session: Session, cafe_id: str, tz: ZoneInfo) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(PurchaseOrder)
        .where(PurchaseOrder.cafe_id == cafe_id, PurchaseOrder.status.in_(("sent", "confirmed", "partially_received")))
        .order_by(PurchaseOrder.requested_delivery_at)
    )
    return [
        {"order_id": o.order_id, "po_number": o.po_number, "supplier": o.supplier_name, "status": o.status,
         "delivery_at": (o.confirmed_delivery_at or o.requested_delivery_at).astimezone(tz).isoformat(),
         "items": sorted({line.ingredient_id for line in o.lines})}
        for o in rows
    ]


def build_reference(session: Session, cafe_id: str, as_of: datetime | None) -> tuple[dict[str, Any], datetime, ZoneInfo]:
    """The planner's context (same reference time and café timezone), trimmed for routing."""
    context = build_planning_context(session, cafe_id, as_of)
    tz = ZoneInfo(context.timezone)
    ref = context.reference
    reference = {
        "cafe_id": cafe_id,
        "cafe_timezone": context.timezone,
        "now": context.as_of.isoformat(),
        "time_windows": time_windows(context.as_of, tz),
        "ingredients": ref["ingredients"],
        "employees": ref["employees"],
        "menu_variations": ref["menu_variations"],
        "upcoming_events": ref["upcoming_events"],
        "open_orders": _open_orders(session, cafe_id, tz),
        "staffing_policy": ref["cafe_policies"].get("staffing", {}),
    }
    return reference, context.as_of, tz


def _read_actions() -> list[dict[str, Any]]:
    return [
        {"name": a.name, "description": a.description, "input_schema": a.input_schema()}
        for a in READ_ACTIONS.values()
    ]


# ---------------------------------------------------------------------------
# Answer (code)
# ---------------------------------------------------------------------------


class ChatRow(BaseModel):
    name: str
    quantity: str | None = None
    unit: str | None = None
    as_of: str | None = None
    estimated: bool = False
    detail: str | None = None
    below_reorder_point: bool = False


class ChatLookup(BaseModel):
    action: str
    args: dict[str, Any]
    summary: str | None = None
    rows: list[ChatRow] = Field(default_factory=list)
    error: str | None = None


def _num(value: Any) -> str | None:
    if value is None:
        return None
    text = f"{value}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _when(value: Any, tz: ZoneInfo) -> str | None:
    moment = local_time(str(value), tz)
    return f"{day(moment)}, {clock(moment)}" if moment else None


def _stock_rows(
    result: list[dict], reorder: dict[str, Any], tz: ZoneInfo, now: datetime, whole_list: bool, summary: str | None
) -> tuple[list[ChatRow], list[str], list[str]]:
    """Rows, the answer and notes for the end. Reorder points only come up when the owner asked about the
    whole list ("are we low on anything"); "do you have X" is answered with what's on hand."""
    rows, low, checked = [], [], 0
    for r in result:
        known = r.get("stock_status") == "known"
        point = reorder.get(r["ingredient_id"])
        detail = []
        counted = local_time(str(r.get("latest_verified_counted_at") or ""), tz)
        if counted:
            detail.append(f"last counted {day(counted)}, {counted.year}")
        is_low = False
        if known and point is not None:
            checked += 1
            if float(r["estimated_quantity"]) < float(point):
                is_low = True
                low.append(f"{r['name']} ({quantity(r['estimated_quantity'], r.get('base_unit'))} left, reorder at "
                           f"{quantity(point, r.get('base_unit'))})")
                if whole_list:
                    detail.append(f"below reorder point {quantity(point, r.get('base_unit'))}")
        rows.append(ChatRow(
            name=r["name"],
            quantity=_num(r["estimated_quantity"]) if known else None,
            unit=r.get("base_unit"),
            as_of=(local_time(str(r.get("as_of") or ""), tz) or now).isoformat(),
            estimated=bool(r.get("included_movement_count") or r.get("has_estimated_movements")),
            detail="; ".join(detail) or (None if known else f"stock {r.get('stock_status')}"),
            below_reorder_point=is_low,
        ))
    rows.sort(key=lambda row: not row.below_reorder_point)  # low items first, order kept
    lead = ("Below reorder point: " + "; ".join(low) + ".") if low else None
    unsure = [r["name"] for r in result if r.get("stock_status") != "known"]
    if whole_list:  # "are we low on anything": the low items, not a count of everything
        if lead:
            lines = [lead]
        elif checked:
            lines = ["Nothing is below its reorder point."]
        else:  # needs reorder points; say so rather than guess
            lines = ["No reorder points are set in the stock list, so I can't tell what's low."]
        notes = [f"Not sure about {', '.join(unsure)}."] if unsure else []
        return rows, lines, notes
    return rows, [summary] if summary else [], []


def _rows(action: str, result: Any, args: dict[str, Any], names: dict[str, str], tz: ZoneInfo) -> list[ChatRow]:
    if action == "sales_summary" and isinstance(result, dict):
        end = result.get("end_at")
        return [ChatRow(name=p["name"], quantity=_num(p["quantity"]), unit="sold", as_of=str(end) if end else None)
                for p in result.get("top_products", [])]
    if action == "list_shifts" and isinstance(result, list):
        return [ChatRow(name=s.get("employee_name") or s["employee_id"], detail=span(s["start_at"], s["end_at"], tz))
                for s in result if s.get("status", "scheduled") == "scheduled"]
    if action == "list_employees" and isinstance(result, list):
        return [ChatRow(name=e["name"], detail=e.get("role")) for e in result if e.get("active", True)]
    if action == "coverage_report" and isinstance(result, dict):
        return [ChatRow(name=_when(h["hour_start"], tz), quantity=_num(h["scheduled_employees"]),
                        unit=f"of {h['required_staff']} staff") for h in result.get("hours", [])]
    if action == "list_deliveries" and isinstance(result, dict):
        seen: set[tuple[str, str]] = set()
        rows = []
        for d in result.get("deliveries", []):  # every order line, and the next regular delivery of each item
            key = (d["kind"], d["ingredient_id"])
            if d["kind"] == "regular" and key in seen:
                continue
            seen.add(key)
            source = f"{d['supplier']} {d.get('po_number') or ''}, {d['status']}".replace(" ,", ",") if d["kind"] == "order" else "regular delivery"
            rows.append(ChatRow(name=d["name"], quantity=_num(d["quantity"]), unit=d["unit"],
                                detail=f"{_when(d['arrives_at'], tz)} · {source}"))
        return rows
    if action == "get_purchase_order" and isinstance(result, dict):
        delivery = result.get("confirmed_delivery_at") or result.get("requested_delivery_at")
        when = _when(delivery, tz) if delivery else None
        return [ChatRow(name=names.get(l["ingredient_id"], l["ingredient_id"]), quantity=_num(l["quantity"]), unit=l["unit"],
                        detail=f"{result['supplier_name']}, {result['status']}" + (f", delivery {when}" if when else ""))
                for l in result.get("lines", [])]
    if action == "estimate_event_demand" and isinstance(result, dict):
        drinks, food = result.get("drinks") or {}, result.get("food") or {}
        return [ChatRow(name="Extra drinks (typical)", quantity=_num(drinks.get("extra_typical")), unit="drinks", estimated=True,
                        detail=f"range {drinks.get('extra_low')} to {drinks.get('extra_high')}"),
                ChatRow(name="Extra pastries (typical)", quantity=_num(food.get("extra_typical")), unit="pastries", estimated=True)]
    if action == "project_recipe_usage" and isinstance(result, dict):
        return [ChatRow(name=i["name"], quantity=_num(i["quantity"]), unit=i.get("unit")) for i in result.get("ingredients", [])]
    if action == "calculate_shortfall" and isinstance(result, dict):
        return [ChatRow(name=l["name"], quantity=_num(l.get("shortfall")), unit=l.get("unit"), detail=l.get("status"))
                for l in result.get("lines", []) if l.get("status") != "enough"]
    if isinstance(result, list):
        return [ChatRow(name=str(r.get("name") or r.get("employee_name") or r.get("sale_id") or r.get("count_id")
                                 or r.get("movement_id") or r.get("ingredient_id") or "item"))
                for r in result[:MAX_ROWS] if isinstance(r, dict)]
    return []


def _ingredient_ids(args: dict[str, Any]) -> list[str]:
    ids = [args["ingredient_id"]] if args.get("ingredient_id") else []
    return ids + list(args.get("ingredient_ids") or [])


# ---------------------------------------------------------------------------
# Spark's reply, checked by code
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


def _numbers(text: str) -> set[Decimal]:
    return {Decimal(n.replace(",", "")).normalize() for n in _NUMBER.findall(text)}


def allowed_numbers(message: str, results: list[Any], lines: list[str], rows: list[dict], notes: list[str]) -> set[Decimal]:
    """Every number the reply may use: the results, what code already wrote (which carries its unit
    conversions, like 1250 mL = 1.25 L), the rows, and the owner's own question."""
    texts = [message, json.dumps(results, default=str), *lines, *notes]
    texts += [f"{r.get('quantity') or ''} {r.get('detail') or ''}" for r in rows]
    found = set().union(*(_numbers(t) for t in texts))
    return found | {(n / 1000).normalize() for n in found}  # mL -> L, g -> kg, as code shows them


def unverified_numbers(reply: str, allowed: set[Decimal]) -> set[Decimal]:
    return {n for n in _numbers(reply) if n not in allowed}


def _reply_row(row: "ChatRow") -> dict[str, Any]:
    """A row as the owner reads it: the amount in the units code shows (5000 mL -> 5 L)."""
    out: dict[str, Any] = {"name": row.name}
    if row.quantity is not None:
        out["amount"] = quantity(row.quantity, row.unit)
    if row.detail:
        out["detail"] = row.detail
    return out


def _reply(
    client: Any, model: str, message: str, lookups: list["ChatLookup"], notes: list[str], cafe_id: str
) -> tuple[str | None, int]:
    """Spark's reply sentences, or None when the call fails (the code answer is used instead)."""
    payload = {"owner_message": message, "code_answer": [l.summary for l in lookups if l.summary], "notes": notes,
               # the rows the owner sees (the first five): a bigger payload makes Spark slower, not better
               "results": [{"lookup": l.action, "rows": [_reply_row(r) for r in l.rows[:5]]} for l in lookups if not l.error]}
    messages = [{"role": "system", "content": REPLY_PROMPT}, {"role": "user", "content": json.dumps(payload, default=str)}]
    record: dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat(), "purpose": "chat_reply", "attempt": 1,
                              "model": model, "query": message}
    try:
        # Writing two sentences from checked results needs little reasoning: minimal keeps it near 4 s.
        response, latency_ms = call_model(client, model=model, messages=messages, temperature=0, reasoning_effort="minimal")
        text = (response.choices[0].message.content or "").strip()
    except Exception as error:  # a reply that fails falls back to code; it never fails the answer
        record.update(latency_ms=getattr(error, "latency_ms", 0), error=f"{type(error).__name__}: {error}")
        _log_call(record)
        record_ai_call(cafe_id=cafe_id, purpose="chat", model=model, latency_ms=record["latency_ms"],
                       request=f"reply: {message}", outcome="timeout" if is_timeout(error) else "failed",
                       error=record["error"])
        return None, record["latency_ms"]
    record.update(latency_ms=latency_ms, response=text)
    _log_call(record)
    record_ai_call(cafe_id=cafe_id, purpose="chat", model=model, latency_ms=latency_ms, request=f"reply: {message}", outcome="ok")
    return text or None, latency_ms


# ---------------------------------------------------------------------------
# Follow-ups (code)
# ---------------------------------------------------------------------------


def _ask(label: str, text: str | None = None) -> dict[str, str]:
    return {"label": label, "kind": "ask", "text": text or label}


def _plan(label: str, text: str) -> dict[str, str]:
    return {"label": label, "kind": "plan", "text": text}


def follow_ups(lookups: list["ChatLookup"], results: dict[str, Any], reference: dict[str, Any], tz: ZoneInfo) -> list[dict[str, str]]:
    """Up to two next steps chosen by code. A "plan" chip hands its text to the plan flow; chat never writes."""
    events = reference.get("upcoming_events") or []
    busy = [e for e in events if e.get("event_type") != "holiday"] or events  # holidays are quieter days
    event_day = local_time(str(busy[0]["starts_at"]), tz).strftime("%A") if busy else None
    chips: list[dict[str, str]] = []
    for lookup in lookups:
        if lookup.error:
            continue
        if lookup.action == "get_stock_estimates":
            low = [r.name for r in lookup.rows if r.below_reorder_point]
            if low:
                chips.append(_plan(f"Plan an order for {low[0].lower()}", f"Order more {low[0].lower()} before we run out"))
                if event_day:
                    chips.append(_ask(f"Check {event_day} stock", f"Do we have enough stock for {event_day}?"))
            else:
                chips.append(_ask("Are we low on anything?"))
        elif lookup.action == "list_deliveries":
            missing = (results.get("list_deliveries") or {}).get("without_delivery") or []
            if missing:
                chips.append(_plan(f"Plan an order for {missing[0].lower()}", f"Order {missing[0].lower()} for this week"))
            chips.append(_ask("Are we low on anything?"))
        elif lookup.action == "sales_summary":
            chips.append(_ask("How were sales this week?"))
        elif lookup.action in ("list_shifts", "coverage_report") and event_day:
            chips.append(_ask(f"Are we covered for {event_day}?", f"Are we covered for the event on {event_day}?"))
    unique: list[dict[str, str]] = []
    for chip in chips:
        if chip["label"] not in {c["label"] for c in unique}:
            unique.append(chip)
    return unique[:2]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _route(
    client: Any, model: str, message: str, reference: dict[str, Any], channel: str, cafe_id: str
) -> tuple[ChatRouting, int]:
    payload = {"read_actions": _read_actions(), "reference": reference, "channel": channel, "owner_message": message}
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": json.dumps(payload, default=str)}]
    record: dict[str, Any] = {"at": datetime.now(timezone.utc).isoformat(), "purpose": "chat", "attempt": 1,
                              "model": model, "query": message}
    try:
        response, latency_ms = call_model(client, model=model, messages=messages, temperature=0, reasoning_effort="low")
    except Exception as error:  # timeouts, API errors, connection failures
        latency_ms = getattr(error, "latency_ms", 0)
        record.update(latency_ms=latency_ms, error=f"{type(error).__name__}: {error}")
        _log_call(record)
        timeout = is_timeout(error)
        record_ai_call(cafe_id=cafe_id, purpose="chat", model=model, latency_ms=latency_ms, request=message,
                       outcome="timeout" if timeout else "failed", error=record["error"])
        if timeout:
            raise ChatTimeoutError(f"Muse didn't answer within {TIMEOUT_SECONDS} seconds. Try again.") from error
        raise ModelCallError(_describe_model_error(error)) from error
    content = response.choices[0].message.content or ""
    record.update(latency_ms=latency_ms, response=content)
    match = _FENCE.match(content)
    try:
        routing = ChatRouting.model_validate(json.loads(match.group(1) if match else content))
    except (json.JSONDecodeError, ValidationError) as error:
        record["error"] = f"unreadable routing: {str(error).splitlines()[0][:200]}"
        _log_call(record)
        record_ai_call(cafe_id=cafe_id, purpose="chat", model=model, latency_ms=latency_ms, request=message,
                       outcome="failed", error="unreadable routing")
        raise ModelCallError("Muse's answer wasn't readable. Try asking again.") from error
    _log_call(record)
    record_ai_call(cafe_id=cafe_id, purpose="chat", model=model, latency_ms=latency_ms, request=message, outcome="ok")
    return routing, latency_ms


def answer(session: Session, client: Any, message: str, *, cafe_id: str, channel: str, as_of: datetime | None) -> dict[str, Any]:
    model = chat_settings().chat_model
    reference, now, tz = build_reference(session, cafe_id, as_of)
    routing, latency_ms = _route(client, model, message, reference, channel, cafe_id)
    out: dict[str, Any] = {"intent": routing.intent, "message": message, "channel": channel, "model": model,
                           "latency_ms": latency_ms, "as_of": now.isoformat(), "lines": [], "rows": [], "notes": [],
                           "lookups": [], "plan_request": None, "reply": None, "reply_by": "code", "follow_ups": []}
    # A write anywhere means the owner wants a change: hand the text to the plan flow, run nothing.
    if routing.intent == "needs_plan" or any(a.action in REGISTRY and a.action not in READ_ACTIONS for a in routing.actions):
        out.update(intent="needs_plan", plan_request=message)
        return out
    if routing.intent == "out_of_scope":
        out["lines"] = ["That's not something I can help with here. Ask about stock, sales, staff, orders or events."]
        out["reply"] = out["lines"][0]
        return out

    known_ids = {i["ingredient_id"] for i in reference["ingredients"]}
    items = session.execute(select(InventoryItem.ingredient_id, InventoryItem.name, InventoryItem.reorder_point)
                            .where(InventoryItem.cafe_id == cafe_id)).all()
    names = {iid: name for iid, name, _ in items}
    reorder = {iid: point for iid, _, point in items if point is not None}
    lookups: list[ChatLookup] = []
    notes: list[str] = []  # small lines after the rows
    results: dict[str, Any] = {}
    try:
        for chosen in routing.actions:
            action = READ_ACTIONS.get(chosen.action)
            args = {**chosen.args, "cafe_id": cafe_id} if action and "cafe_id" in action.input_model.model_fields else dict(chosen.args)
            if action is None:
                lookups.append(ChatLookup(action=chosen.action, args=chosen.args, error="not a read-only lookup"))
                continue
            if chosen.action in ("get_stock_estimates", "list_deliveries"):
                unknown = [i for i in _ingredient_ids(args) if i not in known_ids]
                if unknown:
                    lookups.append(ChatLookup(action=chosen.action, args=args, summary=NOT_ON_LIST))
                    continue
            if chosen.action == "get_stock_estimates":
                args.setdefault("as_of", now.isoformat())
            if chosen.action == "list_deliveries":  # the reference time, never the wall clock
                args.setdefault("start_at", now.isoformat())
            try:
                action.input_model.model_validate(args)
                result = action.run(session, args)
                results[chosen.action] = result
            except ValidationError as error:
                lookups.append(ChatLookup(action=chosen.action, args=args,
                                          error="; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()[:3])))
                continue
            except Exception as error:  # a lookup that fails is reported, never guessed
                lookups.append(ChatLookup(action=chosen.action, args=args, error=str(error).splitlines()[0][:200] or type(error).__name__))
                continue
            node = SimpleNamespace(node_id=chosen.action, action=chosen.action, status="done", side_effect="read",
                                   resolved_args=args, args=args, result=result, error=None, replay_of=None)
            try:
                summary = summarize_step(node, RunContext([node], cafe_id, str(tz)))
            except Exception:  # a summary must never break an answer
                summary = None
            extra: list[str] = []
            if chosen.action == "get_stock_estimates" and isinstance(result, list):
                rows, extra, stock_notes = _stock_rows(result, reorder, tz, now, not _ingredient_ids(args), summary)
                summary = None  # the stock answer is built from the rows above
                notes.extend(stock_notes)
            else:
                rows = _rows(chosen.action, result, args, names, tz)
            if chosen.action == "sales_summary" and len(rows) == 5:
                extra.append("The sales summary lists the top 5 products only.")
            parts = [p if p.endswith((".", "!", "?")) else f"{p}." for p in (summary, *extra) if p]
            lookups.append(ChatLookup(action=chosen.action, args=args, summary=" ".join(parts) or None, rows=rows))
    finally:
        session.rollback()  # the chat path never writes

    out["lookups"] = [l.model_dump() for l in lookups]
    out["notes"] = notes
    out["lines"] = [l.summary for l in lookups if l.summary] or (
        ["I couldn't look that up."] if any(l.error for l in lookups)
        else ["I couldn't find a lookup for that. Ask about stock, sales, staff, orders or events."]
    )
    out["rows"] = [r.model_dump() for l in lookups for r in l.rows][:MAX_ROWS * MAX_ACTIONS]
    out["follow_ups"] = follow_ups(lookups, results, reference, tz)

    # Spark writes the reply; code keeps it only if every number in it is in the results.
    out["reply"] = " ".join(out["lines"])
    if results:
        text, reply_ms = _reply(client, model, message, lookups, notes, cafe_id)
        out["latency_ms"] += reply_ms
        allowed = allowed_numbers(message, list(results.values()), out["lines"], out["rows"], notes)
        if text and not unverified_numbers(text, allowed):
            out.update(reply=text, reply_by="muse")
    return out
