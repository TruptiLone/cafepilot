"""Planning service: ask the model, validate, and give it one chance to fix mistakes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.modules.agent.planner import (
    PlannerError,
    PlannerResult,
    PlanningContext,
    plan_query,
)
from backend.modules.agent.validator import (
    ValidationResult,
    format_issues_for_model,
    validate_plan,
)

MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class PlanningOutcome:
    result: PlannerResult
    validation: ValidationResult
    attempts: int


def plan_and_validate(
    query: str,
    context: PlanningContext,
    client: Any,
    model: str | None = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> PlanningOutcome:
    feedback: tuple[str, str] | None = None
    last_error: PlannerError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = plan_query(query, context, client, model, feedback)
        except PlannerError as error:
            last_error = error
            feedback = (error.raw_content or "", f"- [unparseable] {error}")
            continue
        validation = validate_plan(result.plan, context.cafe_id)
        done = validation.valid or result.plan.status != "plan" or attempt == max_attempts
        if done:
            return PlanningOutcome(result, validation, attempt)
        feedback = (result.raw_content, format_issues_for_model(validation.issues))
    assert last_error is not None
    raise last_error
