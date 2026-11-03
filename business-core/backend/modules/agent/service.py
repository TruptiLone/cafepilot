"""Planning service: ask the model, validate, and give it one chance to fix mistakes."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from backend.db.config import get_settings
from backend.modules.agent.ai_calls import record
from backend.modules.agent.planner import (
    ModelCallError,
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
    ai_call_id: str | None = None  # the ai_calls row; the router links it to the run


def plan_and_validate(
    query: str,
    context: PlanningContext,
    client: Any,
    model: str | None = None,
    max_attempts: int = MAX_ATTEMPTS,
    purpose: str = "plan",
) -> PlanningOutcome:
    """Plan, validate, retry once with the validator's reasons; one ai_calls row for the whole request."""
    model = model or get_settings().planner_model
    started = time.monotonic()
    attempts = 0

    def log(outcome: str, error: str | None = None) -> str | None:
        return record(cafe_id=context.cafe_id, purpose=purpose, model=model, outcome=outcome, request=query,
                      attempts=max(attempts, 1), latency_ms=int((time.monotonic() - started) * 1000), error=error)

    feedback: tuple[str, str] | None = None
    last_error: PlannerError | None = None
    try:
        for attempt in range(1, max_attempts + 1):
            attempts = attempt
            try:
                result = plan_query(query, context, client, model, feedback, purpose=purpose, attempt=attempt)
            except PlannerError as error:
                last_error = error
                feedback = (error.raw_content or "", f"- [unparseable] {error}")
                continue
            validation = validate_plan(result.plan, context.cafe_id, timezone=context.timezone)
            done = validation.valid or result.plan.status != "plan" or attempt == max_attempts
            if done:
                outcome = "ok" if validation.valid and attempt == 1 else "retried" if validation.valid else "failed"
                issues = None if validation.valid else "; ".join(i.message for i in validation.issues[:2])
                return PlanningOutcome(result, validation, attempt, log(outcome, issues))
            feedback = (result.raw_content, format_issues_for_model(validation.issues))
    except ModelCallError as error:
        log("timeout" if error.timeout else "failed", str(error))
        raise
    assert last_error is not None
    log("failed", str(last_error))
    raise last_error
