"""Executor: runs a validated plan step by step and pauses at every write.

Rules
- A step runs only when every step it depends on is ``done``.
- ``$node.path`` arguments are resolved from upstream results, then re-validated
  against the action's input model (the plan was checked before values existed).
- A write stops at ``awaiting_approval``; the owner's decision resumes the run.
  Nothing waits in memory: state lives in Postgres.
- A write whose exact action + resolved arguments already succeeded in an earlier
  run is marked ``done`` as a replay of that run, so re-running a plan never
  places a second order or schedules a second shift.
- If a step fails or is rejected, everything downstream is ``skipped``.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.modules.agent.models import AgentApproval, AgentNodeRun, AgentPlanRun
from backend.modules.agent.planner import Plan
from backend.modules.agent.registry import REGISTRY
from backend.modules.agent.validator import ValidationResult, parse_ref

_PATH_TOKEN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")
_FINISHED_BAD = ("failed", "rejected", "skipped")


class RunNotFoundError(LookupError):
    pass


class NodeNotFoundError(LookupError):
    pass


class ApprovalStateError(RuntimeError):
    """The step cannot be decided on (not a write, or not waiting yet)."""


class EditValidationError(ValueError):
    """The owner's changes do not pass the action's rules."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------


def resolve_path(value: Any, path: str) -> Any:
    position = 0
    for match in _PATH_TOKEN.finditer(path):
        if match.start() != position:
            raise KeyError(f"malformed path {path!r}")
        position = match.end()
        key, index = match.groups()
        if key is not None:
            if not isinstance(value, dict) or key not in value:
                raise KeyError(f"no field {key!r}")
            value = value[key]
        else:
            if not isinstance(value, list) or int(index) >= len(value):
                raise IndexError(f"no item [{index}]")
            value = value[int(index)]
    if position != len(path):
        raise KeyError(f"malformed path {path!r}")
    return value


def resolve_args(value: Any, results: dict[str, Any]) -> Any:
    ref = parse_ref(value)
    if ref is not None:
        node_id, path = ref
        if node_id not in results:
            raise KeyError(f"${node_id} has no result")
        return resolve_path(results[node_id], path)
    if isinstance(value, dict):
        return {k: resolve_args(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_args(v, results) for v in value]
    return value


def write_fingerprint(cafe_id: str, action: str, resolved_args: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"cafe_id": cafe_id, "action": action, "args": resolved_args},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def create_run(
    session: Session,
    *,
    query: str,
    cafe_id: str,
    as_of: datetime,
    plan: Plan,
    validation: ValidationResult,
    model: str,
    attempts: int,
) -> AgentPlanRun:
    if not validation.valid:
        raise ValueError("only a valid plan can be run")
    now = _now()
    run = AgentPlanRun(
        run_id=f"RUN-{uuid4().hex[:12].upper()}",
        cafe_id=cafe_id,
        query=query,
        goal=plan.goal,
        assumptions=list(plan.assumptions),
        status="running",
        as_of=as_of,
        model=model,
        attempts=attempts,
        edges=[{"source": e.source, "target": e.target, "kind": e.kind} for e in validation.edges],
        levels=validation.levels,
        warnings=validation.warnings,
        created_at=now,
        updated_at=now,
    )
    for node in validation.nodes:
        run.nodes.append(
            AgentNodeRun(
                node_id=node.id,
                action=node.action,
                side_effect=node.side_effect,
                requires_approval=node.requires_approval,
                reason=node.reason,
                only_if=node.only_if,
                args=node.args,
                depends_on=node.depends_on,
                level=node.level,
                status="pending",
            )
        )
    session.add(run)
    session.commit()
    return run


def get_run(session: Session, run_id: str) -> AgentPlanRun:
    run = session.get(AgentPlanRun, run_id)
    if run is None:
        raise RunNotFoundError(run_id)
    return run


def _finish(node: AgentNodeRun, status: str, *, error: str | None = None) -> None:
    node.status = status
    node.error = error
    node.finished_at = _now()


def _run_status(run: AgentPlanRun) -> str:
    statuses = {node.status for node in run.nodes}
    if "awaiting_approval" in statuses:
        return "awaiting_approval"
    if statuses & {"pending", "approved", "running"}:
        return "running"
    return "completed" if statuses <= {"done"} else "completed_with_issues"


def _prepare(session: Session, run: AgentPlanRun, node: AgentNodeRun, results: dict[str, Any]) -> bool:
    """Resolve and re-validate a step's arguments. Returns False if the step failed."""
    try:
        resolved = resolve_args(node.args, results)
        skip_reason = REGISTRY[node.action].skip_reason
        reason = skip_reason(resolved) if skip_reason else None
        if reason:
            node.resolved_args = resolved
            _finish(node, "skipped", error=reason)
            return False
        REGISTRY[node.action].input_model.model_validate(resolved)
    except (KeyError, IndexError, TypeError) as error:
        _finish(node, "failed", error=f"could not resolve arguments: {error}")
        return False
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors()[:3]
        )
        _finish(node, "failed", error=f"arguments invalid after resolution: {details}")
        return False
    node.resolved_args = resolved
    if node.side_effect == "write":
        node.idempotency_key = write_fingerprint(run.cafe_id, node.action, resolved)
    return True


def _replay_of(session: Session, run: AgentPlanRun, node: AgentNodeRun) -> AgentNodeRun | None:
    return session.scalar(
        select(AgentNodeRun)
        .where(
            AgentNodeRun.idempotency_key == node.idempotency_key,
            AgentNodeRun.status == "done",
            AgentNodeRun.run_id != run.run_id,
        )
        .order_by(AgentNodeRun.finished_at)
        .limit(1)
    )


def _invoke(session: Session, node: AgentNodeRun) -> None:
    node.status = "running"
    node.started_at = _now()
    session.commit()  # services open their own transaction for writes
    action, args = node.action, node.resolved_args
    try:
        result = REGISTRY[action].run(session, args)
    except Exception as error:  # domain errors are reported on the step, not raised
        session.rollback()
        _finish(node, "failed", error=f"{type(error).__name__}: {error}")
        return
    node.result = result
    _finish(node, "done")


def execute(session: Session, run_id: str) -> AgentPlanRun:
    run = get_run(session, run_id)
    progressed = True
    while progressed:
        progressed = False
        by_id = {node.node_id: node for node in run.nodes}
        for node in sorted(run.nodes, key=lambda n: (n.level, n.node_id)):
            if node.status not in ("pending", "approved"):
                continue
            upstream = [by_id[d] for d in node.depends_on]
            blocked = [d.node_id for d in upstream if d.status in _FINISHED_BAD]
            if blocked:
                _finish(node, "skipped", error=f"skipped because {', '.join(blocked)} did not complete")
                progressed = True
                continue
            if any(d.status != "done" for d in upstream):
                continue

            if node.status == "pending":
                results = {n.node_id: n.result for n in run.nodes if n.status == "done"}
                if not _prepare(session, run, node, results):
                    progressed = True
                    continue
                if node.side_effect == "write":
                    prior = _replay_of(session, run, node)
                    if prior is not None:
                        node.result = prior.result
                        node.replay_of = f"{prior.run_id}/{prior.node_id}"
                        _finish(node, "done")
                        progressed = True
                        continue
                    if node.requires_approval:
                        node.status = "awaiting_approval"
                        progressed = True
                        continue

            _invoke(session, node)
            progressed = True

    run.status = _run_status(run)
    run.updated_at = _now()
    session.commit()
    return run


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


def decide(
    session: Session,
    run_id: str,
    node_id: str,
    decision: str,
    *,
    decided_by: str = "owner",
    channel: str = "web",
    note: str | None = None,
    edited_args: dict[str, Any] | None = None,
) -> AgentPlanRun:
    """Approve or reject a waiting write. ``edited_args`` (approve only) replaces fields of
    the step's arguments; the result is re-validated like any model-proposed step."""
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be approved or rejected")
    if edited_args and decision != "approved":
        raise EditValidationError("changes can only be made when approving")
    run = get_run(session, run_id)
    node = next((n for n in run.nodes if n.node_id == node_id), None)
    if node is None:
        raise NodeNotFoundError(node_id)
    if not node.requires_approval:
        raise ApprovalStateError(f"{node_id} is a read step; it needs no approval")
    if node.status != "awaiting_approval":
        already = session.scalar(
            select(AgentApproval).where(AgentApproval.run_id == run_id, AgentApproval.node_id == node_id)
        )
        if already is not None or node.replay_of:
            return run  # double click / repeated voice command: no-op
        raise ApprovalStateError(f"{node_id} is {node.status}, not waiting for approval")

    original = dict(node.resolved_args or {})
    edited: dict[str, Any] | None = None
    if edited_args:
        edited = {**original, **{k: v for k, v in edited_args.items() if k != "cafe_id"}}
        try:
            validated = REGISTRY[node.action].input_model.model_validate(edited)
        except ValidationError as error:
            details = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors()[:3]
            )
            raise EditValidationError(f"Your changes are not valid: {details}") from error
        edited = validated.model_dump(mode="json", exclude_unset=False)
        edited = {k: v for k, v in edited.items() if k in original or k in edited_args}

    session.add(
        AgentApproval(
            approval_id=f"APR-{uuid4().hex[:12].upper()}",
            run_id=run_id,
            node_id=node_id,
            decision=decision,
            decided_by=decided_by,
            channel=channel,
            note=note,
            original_args=original if edited is not None else None,
            edited_args=edited,
            decided_at=_now(),
        )
    )
    if edited is not None and edited != original:
        node.resolved_args = edited
        node.idempotency_key = write_fingerprint(run.cafe_id, node.action, edited)
    if decision == "approved":
        node.status = "approved"
    else:
        _finish(node, "rejected", error=f"rejected by {decided_by}")
    session.commit()
    return execute(session, run_id)


def decide_all(
    session: Session,
    run_id: str,
    decision: str,
    *,
    decided_by: str = "owner",
    channel: str = "web",
) -> AgentPlanRun:
    """Decide every step currently waiting (the "Approve all" button)."""
    run = get_run(session, run_id)
    waiting = [n.node_id for n in run.nodes if n.status == "awaiting_approval"]
    for node_id in waiting:
        run = decide(session, run_id, node_id, decision, decided_by=decided_by, channel=channel)
    return run
