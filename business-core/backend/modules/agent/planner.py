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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.config import get_settings
from backend.models.cafe import Cafe
from backend.modules.agent.cafe_policies import policies_for
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
        "cafe_policies": policies_for(cafe_id),
    }
    return PlanningContext(cafe_id, cafe.timezone, effective_as_of, now, reference)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the planner for CaféPilot, an operations assistant for an independent café.
Turn the owner's request into a small plan: a set of actions from the ACTION LIST.
You only plan. Code validates and runs the plan, and the owner approves every write.

Return ONLY a JSON object, no prose and no code fences:
{
  "status": "plan" | "needs_clarification" | "out_of_scope",
  "goal": "one short sentence",
  "assumptions": ["each assumption you made, e.g. demand mix or staffing threshold"],
  "question": "only when status is needs_clarification: one specific question",
  "nodes": [
    {"id": "snake_case_id", "action": "<name from ACTION LIST>",
     "args": {...matching that action's input_schema...},
     "reason": "why this step is needed, for the owner",
     "only_if": "writes only: the condition the owner should check before approving"}
  ]
}

RULES
1. Use only actions from the ACTION LIST. Never invent actions or argument names.
2. args must match the action's input_schema. Every timestamp must include a timezone offset
   in the café's timezone (see CONTEXT). Use the café's real IDs from REFERENCE DATA;
   never invent IDs.
3. To use a previous step's output, write "$<node_id>" or "$<node_id>.<field>" (use [n] for
   list items) as the argument value. Only reference values that the earlier step returns.
   Prefer literal values from REFERENCE DATA when they are already known.
4. If an action lists must_come_after, include those actions as earlier nodes (one action from
   each group). This is enforced by code.
5. Read first, then write. Add a write only when the request asks for a change. Writes are
   proposals: they wait for owner approval, so never describe them as done. When a write only
   makes sense if an earlier read shows a need (e.g. a shortfall or a coverage gap), keep the
   write and say so in only_if.
6. Demand assumptions are scenarios, not forecasts. State them in assumptions.
   Take staffing thresholds and espresso-capable roles from REFERENCE DATA cafe_policies;
   do not invent them. Only schedule people whose role can do the job.
7. Keep plans short: at most 15 nodes, no duplicate steps.
8. If a required detail is missing and cannot be reasonably assumed (e.g. no date at all),
   return needs_clarification with one question and no nodes. If the request is not about
   running this café, return out_of_scope with no nodes.
"""


def build_messages(query: str, context: PlanningContext) -> list[dict[str, str]]:
    user_content = {
        "context": {
            "cafe_id": context.cafe_id,
            "cafe_timezone": context.timezone,
            "current_time": context.now.isoformat(),
            "knowledge_as_of": context.as_of.isoformat(),
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
