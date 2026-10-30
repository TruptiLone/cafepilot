"""Planner: turns an owner's request into a plan DAG of registered actions.

The model only *proposes*. It sees the action registry (names, schemas,
mandatory predecessors) plus a reference-data snapshot with real IDs, and
returns JSON. Validation (Task 3) and execution (Task 4) are plain code.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.config import get_settings
from backend.models.cafe import Cafe
from backend.modules.agent.cafe_policies import policies_for
from backend.modules.agent.events import upcoming_for_planner
from backend.modules.agent.registry import describe_for_planner
from backend.modules.inventory.models import InventoryItem
from backend.modules.sales.models import MenuProduct, Modifier, ModifierGroup, ProductVariation
from backend.modules.staffing.models import Employee

LLM_LOG_PATH = Path(__file__).resolve().parents[3] / "var" / "llm_calls.jsonl"
MAX_PLAN_NODES = 15
NODE_ID_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"


# ---------------------------------------------------------------------------
# Plan schema (what the model must return)
# ---------------------------------------------------------------------------


class PlanNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=NODE_ID_PATTERN)
    action: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=300)
    only_if: str | None = Field(
        default=None,
        max_length=300,
        description="For writes: the condition the owner should confirm before approving.",
    )


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["plan", "needs_clarification", "out_of_scope"]
    goal: str = Field(min_length=1, max_length=300)
    assumptions: list[str] = Field(default_factory=list)
    question: str | None = None
    nodes: list[PlanNode] = Field(default_factory=list, max_length=MAX_PLAN_NODES)

    @model_validator(mode="after")
    def _status_rules(self) -> "Plan":
        if self.status == "plan" and not self.nodes:
            raise ValueError("a plan needs at least one node")
        if self.status == "needs_clarification":
            if not (self.question and self.question.strip()):
                raise ValueError("needs_clarification requires a question")
        if self.status != "plan" and self.nodes:
            raise ValueError(f"{self.status} must not contain nodes")
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("node ids must be unique")
        return self


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanningContext:
    cafe_id: str
    timezone: str
    as_of: datetime
    now: datetime
    reference: dict[str, Any]


def build_planning_context(
    session: Session, cafe_id: str, as_of: datetime | None = None
) -> PlanningContext:
    cafe = session.get(Cafe, cafe_id)
    if cafe is None:
        raise LookupError(f"cafe not found: {cafe_id}")
    tz = ZoneInfo(cafe.timezone)
    now = datetime.now(timezone.utc).astimezone(tz)
    effective_as_of = (as_of or now).astimezone(tz)

    variations = session.execute(
        select(ProductVariation, MenuProduct)
        .join(MenuProduct, MenuProduct.product_id == ProductVariation.product_id)
        .where(ProductVariation.cafe_id == cafe_id, ProductVariation.active.is_(True))
        .order_by(ProductVariation.variation_id)
    ).all()
    modifiers = session.execute(
        select(Modifier, ModifierGroup)
        .join(ModifierGroup, ModifierGroup.group_id == Modifier.group_id)
        .where(Modifier.cafe_id == cafe_id, Modifier.active.is_(True))
        .order_by(Modifier.modifier_id)
    ).all()
    ingredients = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.cafe_id == cafe_id, InventoryItem.active.is_(True))
        .order_by(InventoryItem.ingredient_id)
    ).all()
    employees = session.scalars(
        select(Employee)
        .where(Employee.cafe_id == cafe_id, Employee.active.is_(True))
        .order_by(Employee.employee_id)
    ).all()

    reference = {
        "menu_variations": [
            {
                "variation_id": v.variation_id,
                "product": p.name,
                "size": v.name,
                "size_oz": v.size_oz,
            }
            for v, p in variations
        ],
        "modifiers": [
            {"modifier_id": m.modifier_id, "name": m.name, "group": g.name}
            for m, g in modifiers
        ],
        "ingredients": [
            {"ingredient_id": i.ingredient_id, "name": i.name, "base_unit": i.base_unit}
            for i in ingredients
        ],
        "employees": [
            {"employee_id": e.employee_id, "name": e.name, "role": e.role}
            for e in employees
        ],
        "cafe_policies": {k: v for k, v in policies_for(cafe_id).items() if k != "standing_deliveries"},
        "upcoming_events": upcoming_for_planner(session, cafe_id, effective_as_of),
    }
    return PlanningContext(cafe_id, cafe.timezone, effective_as_of, now, reference)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the planner for CaféPilot, an operations assistant for an independent café.
Turn the owner's request into a small plan of actions from ACTION LIST. You only plan:
code validates every step, runs it, and the owner approves every change. Code rejects plans
that break the rules below and sends them back to you, so follow them exactly.

OUTPUT
Return ONLY one JSON object, no prose, no code fences:
{
  "status": "plan" | "needs_clarification" | "out_of_scope",
  "goal": "one short sentence in the owner's words",
  "assumptions": ["every assumption you made, in plain words"],
  "question": "only for needs_clarification: one specific question",
  "nodes": [
    {"id": "snake_case_id", "action": "<name from ACTION LIST>",
     "args": {...exactly the action's input_schema...},
     "reason": "why this step is needed, in plain words for the owner",
     "only_if": "writes only: what the owner should check before approving"}
  ]
}

ACTIONS AND ARGUMENTS
1. Use only actions in ACTION LIST and only argument names in their input_schema.
2. Use only IDs that appear in REFERENCE DATA (variation_id, modifier_id, ingredient_id,
   employee_id). Never invent or shorten an ID.
3. To pass a previous step's output, use "$<node_id>" or "$<node_id>.<field>" ([n] for list items).
4. If an action has must_come_after, include one action from each group as an earlier node.

NUMBERS: CODE DOES THE MATH, NOT YOU
5. Never calculate quantities yourself. For any supplier order, first add a calculate_shortfall
   step, then set the order's lines to exactly "$<that step id>.order_lines". The buffer comes
   from café policy; do not add your own.
6. Demand for an event in REFERENCE DATA upcoming_events comes from history, not from you: add
   estimate_event_demand for that event_id, then give calculate_shortfall
   items "$<demand step id>.scenarios.typical.items" and include_normal_use true. Only when the
   owner states a product mix themselves (or no event applies) write the items directly from
   their words and state them in assumptions.

TIME
7. Every timestamp needs the café's UTC offset FOR THAT DATE, taken from CONTEXT
   utc_offset_by_date (daylight saving changes it). Use knowledge_as_of from CONTEXT as as_of
   for stock unless the owner names another time.

STAFFING
8. Take thresholds and espresso-capable roles from REFERENCE DATA cafe_policies; never guess them.
   Before schedule_shift include coverage_report, list_employees and list_shifts.
9. Only schedule people whose role fits the work, one shift per person per day, and prefer people
   with no shift that day. Propose the fewest shifts that reach the target, one node per shift.

WRITES
10. Read first, then write. Add a write only when the owner asked for that kind of change.
    Writes are proposals waiting for approval: never describe them as done. If a write only makes
    sense when an earlier read shows a need, keep it and say so in only_if.

SCOPE
11. At most 15 nodes, no duplicate steps.
12. If a detail that cannot reasonably be assumed is missing (e.g. no date), return
    needs_clarification with one question and no nodes. If the request is not about running this
    café, return out_of_scope with no nodes.
"""


def utc_offsets_by_date(timezone_name: str, start: datetime, days: int = 14) -> dict[str, str]:
    """Café-local UTC offset at noon for each date, so the model never guesses across DST."""
    tz = ZoneInfo(timezone_name)
    first = start.astimezone(tz).date() - timedelta(days=3)
    offsets = {}
    for index in range(days + 3):
        day = first + timedelta(days=index)
        noon = datetime(day.year, day.month, day.day, 12, tzinfo=tz)
        offsets[day.isoformat()] = noon.strftime("%z")[:3] + ":" + noon.strftime("%z")[3:]
    return offsets


def build_messages(query: str, context: PlanningContext) -> list[dict[str, str]]:
    user_content = {
        "context": {
            "cafe_id": context.cafe_id,
            "cafe_timezone": context.timezone,
            "current_time": context.now.isoformat(),
            "knowledge_as_of": context.as_of.isoformat(),
            "utc_offset_by_date": utc_offsets_by_date(context.timezone, context.now),
            "note": "Use knowledge_as_of as the as_of argument for stock estimates.",
        },
        "action_list": describe_for_planner(),
        "reference_data": context.reference,
        "owner_request": query,
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(user_content, default=str)},
    ]


# ---------------------------------------------------------------------------
# Model call
# ---------------------------------------------------------------------------


class ChatClient(Protocol):
    chat: Any


class PlannerError(RuntimeError):
    """The model did not return a usable plan."""

    def __init__(self, message: str, raw_content: str | None = None) -> None:
        super().__init__(message)
        self.raw_content = raw_content


class PlannerUnavailableError(RuntimeError):
    """The model API is not configured."""


class ModelCallError(RuntimeError):
    """The model API could not be reached or returned an error (not retried)."""


@dataclass(frozen=True)
class PlannerResult:
    plan: Plan
    model: str
    latency_ms: int
    usage: dict[str, Any] | None
    raw_content: str


def create_planner_client() -> Any:
    settings = get_settings()
    key = settings.model_api_key.get_secret_value().strip() if settings.model_api_key else ""
    if not key:
        raise PlannerUnavailableError("MODEL_API_KEY is not set in business-core/.env")
    from openai import OpenAI

    return OpenAI(
        api_key=key,
        base_url=settings.model_api_base_url,
        timeout=60.0,
        max_retries=1,
    )


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def parse_plan(content: str) -> Plan:
    match = _FENCE.match(content)
    text = match.group(1) if match else content
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise PlannerError(f"model returned invalid JSON: {error.msg}") from error
    try:
        return Plan.model_validate(payload)
    except ValidationError as error:
        raise PlannerError(f"model returned a malformed plan: {error.errors()[:3]}") from error


def _log_call(record: dict[str, Any]) -> None:
    try:
        LLM_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LLM_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass  # logging must never break planning


def _describe_model_error(error: Exception) -> str:
    status = getattr(error, "status_code", None)
    name = type(error).__name__
    if status == 401:
        return "The Meta Model API rejected the API key (401). Check MODEL_API_KEY in business-core/.env."
    if status == 429:
        return "The Meta Model API is rate-limiting requests (429). Wait a minute and try again."
    if "Timeout" in name:
        return "The Meta Model API took too long to answer. Try again."
    if "Connection" in name:
        return "Could not reach the Meta Model API. Check your internet connection."
    detail = str(error).splitlines()[0][:200] if str(error) else name
    return f"The Meta Model API returned an error ({status or name}): {detail}"


def plan_query(
    query: str,
    context: PlanningContext,
    client: ChatClient,
    model: str | None = None,
    feedback: tuple[str, str] | None = None,
) -> PlannerResult:
    """Ask the model for a plan.

    ``feedback`` is (previous_response, problems): the earlier answer is replayed and
    the model is asked to return a corrected, complete plan.
    """
    model = model or get_settings().planner_model
    messages = build_messages(query, context)
    if feedback is not None:
        previous, problems = feedback
        messages += [
            {"role": "assistant", "content": previous},
            {
                "role": "user",
                "content": "Your plan was rejected by the validator for these reasons:\n"
                f"{problems}\nReturn the complete corrected plan as JSON only.",
            },
        ]
    started = time.monotonic()
    try:
        response = client.chat.completions.create(model=model, messages=messages, temperature=0)
    except Exception as error:  # openai.APIError, timeouts, connection failures
        _log_call(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "purpose": "replan" if feedback else "plan",
                "model": model,
                "query": query,
                "error": f"{type(error).__name__}: {error}",
            }
        )
        raise ModelCallError(_describe_model_error(error)) from error
    latency_ms = int((time.monotonic() - started) * 1000)

    content = response.choices[0].message.content
    usage = getattr(response, "usage", None)
    usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else usage
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "purpose": "replan" if feedback else "plan",
        "model": model,
        "latency_ms": latency_ms,
        "usage": usage_dict,
        "query": query,
        "response": content,
    }
    try:
        if not isinstance(content, str) or not content.strip():
            raise PlannerError("model returned no text")
        plan = parse_plan(content)
    except PlannerError as error:
        record["error"] = str(error)
        _log_call(record)
        raise PlannerError(str(error), content if isinstance(content, str) else None) from error
    _log_call(record)
    return PlannerResult(plan, model, latency_ms, usage_dict, content)
