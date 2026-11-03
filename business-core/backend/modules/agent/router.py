from datetime import datetime
from typing import Any

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.session import get_db
from backend.modules.agent.planner import (
    ModelCallError,
    Plan,
    PlannerError,
    PlannerUnavailableError,
    build_planning_context,
    create_planner_client,
    read_calls,
)
from backend.models.cafe import Cafe
from backend.modules.agent.ai_calls import link_run, recent as recent_ai_calls
from backend.modules.agent.updates import updates as cafe_updates
from backend.modules.agent.chat import ChatTimeoutError, answer as chat_answer, create_chat_client
from backend.modules.agent.events import DetectedEvent, detected_events, get_event
from backend.modules.agent.status import CafeStatus, cafe_status, event_id_for_run, event_plan
from backend.modules.agent.summaries import summarize_run
from backend.modules.agent.acknowledgements import Acknowledgement, Stage, change_statuses
from backend.modules.purchasing.service import settle_due
from backend.modules.agent.replanning import NothingToReplanError, replan_request, replans_of
from backend.modules.agent.executor import (
    ApprovalStateError,
    EditValidationError,
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


def _plan(
    request: PlanRequest, database_session: Session, client: Any, purpose: str = "plan", event_id: str | None = None
) -> tuple[PlanningOutcome, Any]:
    try:
        context = build_planning_context(database_session, request.cafe_id, request.as_of, event_id=event_id)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cafe not found") from error
    try:
        outcome = plan_and_validate(request.query, context, client, purpose=purpose)
    except (PlannerError, ModelCallError) as error:
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
    summary: str | None = Field(
        default=None, description="One plain line about this step, written by code from its real result."
    )
    acknowledgement: Acknowledgement | None = Field(
        default=None, description="For finished changes: what happened, details, and an honest caveat."
    )
    lifecycle: list[Stage] | None = Field(
        default=None, description="Changes only: Proposed → Approved → Sent/Offered → Confirmed/Accepted or Rejected/Declined."
    )
    external_status: str | None = Field(
        default=None, description="sent / confirmed / rejected (orders), offered / accepted / declined (shifts)."
    )
    replanned_in: str | None = Field(default=None, description="The run that replanned this failed change.")


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
    closing_message: str | None = Field(
        default=None, description="Muse's closing line once every change has been decided."
    )
    waiting_on: list[str] = Field(
        default_factory=list, description="Suppliers or staff whose answer is still pending (poll while non-empty)."
    )
    parent_run_id: str | None = None
    parent_node_id: str | None = None
    event: DetectedEvent | None = Field(default=None, description="The event this plan is for, if any.")


def _run_out(database_session: Session, run: Any) -> RunOut:
    """Run with a code-written one-line summary on every step."""
    cafe = database_session.get(Cafe, run.cafe_id)
    summaries = summarize_run(list(run.nodes), run.cafe_id, cafe.timezone if cafe else "UTC")
    if settle_due(database_session, cafe_id=run.cafe_id):  # reveal simulated supplier / staff answers that are due
        database_session.commit()
    out = RunOut.model_validate(run)
    statuses, closing, waiting = change_statuses(database_session, run)
    replans = replans_of(database_session, run.run_id)
    for node in out.nodes:
        node.replanned_in = replans.get(node.node_id)
        node.summary = summaries.get(node.node_id)
        status_ = statuses.get(node.node_id)
        if status_ is not None:
            node.acknowledgement = status_.acknowledgement
            node.lifecycle = status_.lifecycle
            node.external_status = status_.external_status
            if status_.acknowledgement and status_.acknowledgement.tone in ("done", "waiting", "problem"):
                node.summary = status_.acknowledgement.headline
    out.closing_message = closing
    out.waiting_on = waiting
    event_id = event_id_for_run(database_session, run)
    out.event = get_event(database_session, event_id) if event_id else None
    return out


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
    edited_args: dict[str, Any] | None = Field(
        default=None, description="Approve with changes: fields that replace the step's arguments."
    )


@router.post("/runs", response_model=CreateRunResponse)
def start_run(
    request: PlanRequest,
    database_session: Session = Depends(get_db),
    client: Any = Depends(get_planner_client),
) -> CreateRunResponse:
    """Plan, validate and start running: reads execute now, writes wait for approval."""
    return _start(request, database_session, client)


def _start(
    request: PlanRequest,
    database_session: Session,
    client: Any,
    parent_run_id: str | None = None,
    parent_node_id: str | None = None,
    event_id: str | None = None,
) -> CreateRunResponse:
    purpose = "replan" if parent_run_id else "plan"
    outcome, context = _plan(request, database_session, client, purpose, event_id)
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
        parent_run_id=parent_run_id,
        parent_node_id=parent_node_id,
    )
    link_run(outcome.ai_call_id, run.run_id)
    run = execute(database_session, run.run_id)
    return CreateRunResponse(planning=planning, run=_run_out(database_session, run))


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
    return _run_out(database_session, _load(database_session, run_id))


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
            edited_args=request.edited_args,
        )
    except EditValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except NodeNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Step not found") from error
    except ApprovalStateError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return _run_out(database_session, run)


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
    return _run_out(database_session, run)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=1000)
    channel: Literal["text", "voice"] = "text"
    cafe_id: str = "CAFE001"
    as_of: datetime | None = Field(default=None, description="Reference time for 'today'; defaults to now, like plans.")

    @field_validator("as_of")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("as_of must include a UTC offset")
        return value


def get_chat_client() -> Any:
    try:
        return create_chat_client()
    except PlannerUnavailableError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error


@router.post("/chat")
def chat(request: ChatRequest, database_session: Session = Depends(get_db), client: Any = Depends(get_chat_client)) -> dict:
    """Ask Muse a question. Read-only: Spark picks lookups, code runs them and writes the answer.
    A request to change something comes back as intent needs_plan with plan_request for the plan flow."""
    try:
        return chat_answer(database_session, client, request.message, cafe_id=request.cafe_id,
                           channel=request.channel, as_of=request.as_of)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cafe not found") from error
    except ChatTimeoutError as error:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(error)) from error
    except ModelCallError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error


class UpdateOut(BaseModel):
    id: str
    kind: str
    group: Literal["staff", "orders", "plans"]
    at: str
    title: str
    detail: str | None
    simulated: bool
    run_id: str | None
    model: str | None


@router.get("/updates", response_model=list[UpdateOut])
def list_updates(
    cafe_id: str = Query("CAFE001"),
    since: datetime | None = Query(None, description="Only changes after this time (with UTC offset)."),
    limit: int = Query(50, ge=1, le=200),
    database_session: Session = Depends(get_db),
) -> list[UpdateOut]:
    """Status changes for the Updates bell, newest first. Simulated replies that are due are settled first."""
    if since is not None and since.tzinfo is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="since must include a UTC offset")
    settle_due(database_session, cafe_id=cafe_id)
    database_session.commit()
    try:
        return [UpdateOut(**u) for u in cafe_updates(database_session, cafe_id, since, limit)]
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cafe not found") from error


class AICallOut(BaseModel):
    """One model request: short summary only, never a prompt or a key."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    cafe_id: str | None
    created_at: datetime
    purpose: str
    model: str
    latency_ms: int
    attempts: int
    outcome: str
    run_id: str | None
    request_summary: str
    error: str | None


@router.get("/ai-calls", response_model=list[AICallOut])
def list_ai_calls(
    cafe_id: str = Query("CAFE001"),
    limit: int = Query(50, ge=1, le=500),
    database_session: Session = Depends(get_db),
) -> list[AICallOut]:
    """Read-only: model requests (plans, replans, chat), newest first."""
    return [AICallOut.model_validate(row) for row in recent_ai_calls(database_session, cafe_id, limit)]


class ModelCallOut(BaseModel):
    """One model call from var/llm_calls.jsonl. The request text and raw answer stay in the file."""

    at: datetime | None = None
    model: str | None = None
    purpose: str | None = Field(None, description="plan, replan (after a change fell through), or a vision read")
    attempt: int | None = Field(None, description="1 for the first try, 2 after the validator sent it back")
    latency_ms: int | None = None
    ok: bool
    error: str | None = None


@router.get("/model-calls", response_model=list[ModelCallOut])
def list_model_calls(limit: int = Query(100, ge=1, le=500)) -> list[ModelCallOut]:
    """Read-only: the newest model calls, failed ones included, newest first."""
    return [
        ModelCallOut(
            at=record.get("at"),
            model=record.get("model"),
            purpose=record.get("purpose"),
            attempt=record.get("attempt"),
            latency_ms=record.get("latency_ms"),
            ok=not record.get("error"),
            error=record.get("error"),
        )
        for record in read_calls(limit)
    ]


# ---------------------------------------------------------------------------
# Events: the trigger that starts a plan
# ---------------------------------------------------------------------------


@router.get("/status", response_model=CafeStatus)
def read_cafe_status(cafe_id: str = Query("CAFE001"), database_session: Session = Depends(get_db)) -> CafeStatus:
    """The read-only café panel: stock for the next busy event, staff cover, approvals, sales, events."""
    settle_due(database_session, cafe_id=cafe_id)
    database_session.commit()
    try:
        return cafe_status(database_session, cafe_id)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cafe not found") from error


@router.get("/events", response_model=list[DetectedEvent])
def list_events(cafe_id: str = Query("CAFE001"), database_session: Session = Depends(get_db)) -> list[DetectedEvent]:
    """Upcoming events with expected demand computed from sales history (is_demo marks demo data)."""
    events = detected_events(database_session, cafe_id)
    for event in events:
        event.run_id, event.latest_run_id, event.plan_state = event_plan(database_session, event.event_id)
    return events


@router.get("/events/{event_id}", response_model=DetectedEvent)
def read_event(event_id: str, database_session: Session = Depends(get_db)) -> DetectedEvent:
    event = get_event(database_session, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    return event


@router.post("/events/{event_id}/runs", response_model=CreateRunResponse)
def start_run_for_event(
    event_id: str,
    database_session: Session = Depends(get_db),
    client: Any = Depends(get_planner_client),
) -> CreateRunResponse:
    """The owner said "Yes, check": plan and start running for this event."""
    event = get_event(database_session, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    request = PlanRequest(query=event.request, cafe_id=event.cafe_id, as_of=event.knowledge_as_of)
    return _start(request, database_session, client, event_id=event_id)


@router.post("/runs/{run_id}/nodes/{node_id}/replan", response_model=CreateRunResponse)
def replan_step(
    run_id: str,
    node_id: str,
    database_session: Session = Depends(get_db),
    client: Any = Depends(get_planner_client),
) -> CreateRunResponse:
    """A supplier rejected an order or an employee declined a shift: plan another way."""
    run = _load(database_session, run_id)
    try:
        query = replan_request(database_session, run, node_id)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Step not found") from error
    except NothingToReplanError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    request = PlanRequest(query=query, cafe_id=run.cafe_id, as_of=run.as_of)
    return _start(request, database_session, client, parent_run_id=run_id, parent_node_id=node_id,
                  event_id=event_id_for_run(database_session, run))
