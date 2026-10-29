from datetime import datetime
from typing import Any

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.session import get_db
from backend.modules.agent.planner import (
    Plan,
    PlannerError,
    PlannerUnavailableError,
    build_planning_context,
    create_planner_client,
)
from backend.modules.agent.executor import (
    ApprovalStateError,
    NodeNotFoundError,
    RunNotFoundError,
    create_run,
    decide,
    decide_all,
    execute,
    get_run,
)
from backend.modules.agent.models import AgentPlanRun
from backend.modules.agent.service import PlanningOutcome, plan_and_validate
from backend.modules.agent.validator import ValidationResult

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
    validation: ValidationResult
    attempts: int
    model: str
    latency_ms: int
    as_of: datetime
    planned_at: datetime


def get_planner_client() -> Any:
    try:
        return create_planner_client()
    except PlannerUnavailableError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error


def _plan(request: PlanRequest, database_session: Session, client: Any) -> tuple[PlanningOutcome, Any]:
    try:
        context = build_planning_context(database_session, request.cafe_id, request.as_of)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cafe not found") from error
    try:
        outcome = plan_and_validate(request.query, context, client)
    except PlannerError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
    return outcome, context


def _plan_response(outcome: PlanningOutcome, context: Any) -> PlanResponse:
    return PlanResponse(
        plan=outcome.result.plan,
        validation=outcome.validation,
        attempts=outcome.attempts,
        model=outcome.result.model,
        latency_ms=outcome.result.latency_ms,
        as_of=context.as_of,
        planned_at=context.now,
    )


@router.post("/plans", response_model=PlanResponse)
def create_plan(
    request: PlanRequest,
    database_session: Session = Depends(get_db),
    client: Any = Depends(get_planner_client),
) -> PlanResponse:
    """Ask the model for a plan and validate it, without running anything.

    A rejected plan is returned with ``validation.valid = false`` and the reasons,
    after one automatic retry in which the model sees those reasons.
    """
    outcome, context = _plan(request, database_session, client)
    return _plan_response(outcome, context)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class NodeRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node_id: str
    action: str
    side_effect: str
    requires_approval: bool
    reason: str
    only_if: str | None
    args: dict[str, Any]
    resolved_args: dict[str, Any] | None
    depends_on: list[str]
    level: int
    status: str
    result: Any | None
    error: str | None
    replay_of: str | None
    started_at: datetime | None
    finished_at: datetime | None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    cafe_id: str
    query: str
    goal: str
    assumptions: list[str]
    status: str
    as_of: datetime
    model: str
    attempts: int
    edges: list[dict[str, Any]]
    levels: list[list[str]]
    warnings: list[str]
    created_at: datetime
    updated_at: datetime
    nodes: list[NodeRunOut]


class RunSummaryOut(BaseModel):
    run_id: str
    goal: str
    status: str
    created_at: datetime
    steps: int
    awaiting_approval: int


class CreateRunResponse(BaseModel):
    planning: PlanResponse
    run: RunOut | None = Field(description="Null when the plan was not valid or asked a question.")


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    decided_by: str = Field(default="owner", min_length=1, max_length=100)
    channel: Literal["web", "voice", "glasses", "api"] = "web"
    note: str | None = Field(default=None, max_length=500)


@router.post("/runs", response_model=CreateRunResponse)
def start_run(
    request: PlanRequest,
    database_session: Session = Depends(get_db),
    client: Any = Depends(get_planner_client),
) -> CreateRunResponse:
    """Plan, validate and start running: reads execute now, writes wait for approval."""
    outcome, context = _plan(request, database_session, client)
    planning = _plan_response(outcome, context)
    if not outcome.validation.valid:
        return CreateRunResponse(planning=planning, run=None)
    run = create_run(
        database_session,
        query=request.query,
        cafe_id=request.cafe_id,
        as_of=context.as_of,
        plan=outcome.result.plan,
        validation=outcome.validation,
        model=outcome.result.model,
        attempts=outcome.attempts,
    )
    run = execute(database_session, run.run_id)
    return CreateRunResponse(planning=planning, run=RunOut.model_validate(run))


@router.get("/runs", response_model=list[RunSummaryOut])
def list_runs(
    cafe_id: str = Query("CAFE001"),
    limit: int = Query(20, ge=1, le=100),
    database_session: Session = Depends(get_db),
) -> list[RunSummaryOut]:
    runs = database_session.scalars(
        select(AgentPlanRun)
        .where(AgentPlanRun.cafe_id == cafe_id)
        .order_by(AgentPlanRun.created_at.desc())
        .limit(limit)
    ).all()
    return [
        RunSummaryOut(
            run_id=run.run_id,
            goal=run.goal,
            status=run.status,
            created_at=run.created_at,
            steps=len(run.nodes),
            awaiting_approval=sum(n.status == "awaiting_approval" for n in run.nodes),
        )
        for run in runs
    ]


def _load(database_session: Session, run_id: str) -> AgentPlanRun:
    try:
        return get_run(database_session, run_id)
    except RunNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from error


@router.get("/runs/{run_id}", response_model=RunOut)
def read_run(run_id: str, database_session: Session = Depends(get_db)) -> RunOut:
    return RunOut.model_validate(_load(database_session, run_id))


@router.post("/runs/{run_id}/nodes/{node_id}/decision", response_model=RunOut)
def decide_step(
    run_id: str,
    node_id: str,
    request: DecisionRequest,
    database_session: Session = Depends(get_db),
) -> RunOut:
    """Approve or reject one write. Repeating a decision is a safe no-op."""
    _load(database_session, run_id)
    try:
        run = decide(
            database_session,
            run_id,
            node_id,
            request.decision,
            decided_by=request.decided_by,
            channel=request.channel,
            note=request.note,
        )
    except NodeNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Step not found") from error
    except ApprovalStateError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return RunOut.model_validate(run)


@router.post("/runs/{run_id}/decision", response_model=RunOut)
def decide_all_steps(
    run_id: str,
    request: DecisionRequest,
    database_session: Session = Depends(get_db),
) -> RunOut:
    """Approve or reject every step currently waiting ("Approve all")."""
    _load(database_session, run_id)
    run = decide_all(
        database_session, run_id, request.decision, decided_by=request.decided_by, channel=request.channel
    )
    return RunOut.model_validate(run)
