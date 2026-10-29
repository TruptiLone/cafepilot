from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from backend.db.session import get_db
from backend.modules.agent.planner import (
    Plan,
    PlannerError,
    PlannerUnavailableError,
    build_planning_context,
    create_planner_client,
    plan_query,
)

router = APIRouter(tags=["Agent"])


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    cafe_id: str = "CAFE001"
    as_of: datetime | None = Field(
        default=None, description="Knowledge cutoff for the plan; defaults to now."
    )

    @field_validator("as_of")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must include a timezone")
        return value


class PlanResponse(BaseModel):
    plan: Plan
    model: str
    latency_ms: int
    as_of: datetime
    planned_at: datetime


def get_planner_client() -> Any:
    try:
        return create_planner_client()
    except PlannerUnavailableError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error


@router.post("/plans", response_model=PlanResponse)
def create_plan(
    request: PlanRequest,
    database_session: Session = Depends(get_db),
    client: Any = Depends(get_planner_client),
) -> PlanResponse:
    """Ask the model for a plan. Nothing is validated or executed yet (Tasks 3-4)."""
    try:
        context = build_planning_context(database_session, request.cafe_id, request.as_of)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cafe not found") from error
    try:
        result = plan_query(request.query, context, client)
    except PlannerError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
    return PlanResponse(
        plan=result.plan,
        model=result.model,
        latency_ms=result.latency_ms,
        as_of=context.as_of,
        planned_at=context.now,
    )
