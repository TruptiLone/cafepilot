"""Validator: checks a model-proposed plan against the registry and superset DAG.

Nothing the model proposes runs until this passes. The validator never trusts the
model's ordering: it derives data edges from ``$node`` references and adds the
superset's mandatory safety edges itself, then checks for cycles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo
from graphlib import CycleError, TopologicalSorter
from typing import Any, Literal

from pydantic import ValidationError

from backend.modules.agent.planner import Plan, PlanNode
from backend.modules.agent.registry import REGISTRY
from backend.modules.agent.superset import SupersetDAG, get_superset

REF_PATTERN = re.compile(r"^\$([a-z][a-z0-9_]{0,31})((?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*)$")

IssueCode = Literal[
    "not_a_plan",
    "unknown_action",
    "invalid_args",
    "unknown_ref",
    "self_ref",
    "disallowed_dependency",
    "missing_prerequisite",
    "cafe_mismatch",
    "cycle",
    "wrong_time_offset",
    "unverified_quantity",
    "unrelated_prerequisite",
    "unverified_demand",
]


@dataclass(frozen=True)
class PlanIssue:
    code: IssueCode
    message: str
    node_id: str | None = None


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    kind: Literal["data", "safety"]


@dataclass(frozen=True)
class ValidatedNode:
    id: str
    action: str
    args: dict[str, Any]
    reason: str
    only_if: str | None
    side_effect: str
    requires_approval: bool
    depends_on: list[str]
    level: int


@dataclass
class ValidationResult:
    valid: bool
    issues: list[PlanIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    nodes: list[ValidatedNode] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    levels: list[list[str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# $ref helpers (shared with the executor)
# ---------------------------------------------------------------------------


def parse_ref(value: Any) -> tuple[str, str] | None:
    """Return (node_id, path) if value is a whole-string $reference."""
    if not isinstance(value, str):
        return None
    match = REF_PATTERN.match(value)
    return (match.group(1), match.group(2)) if match else None


def find_refs(value: Any) -> list[tuple[str, str]]:
    if isinstance(value, dict):
        return [ref for item in value.values() for ref in find_refs(item)]
    if isinstance(value, list):
        return [ref for item in value for ref in find_refs(item)]
    ref = parse_ref(value)
    return [ref] if ref else []


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


_TIME_KEY = re.compile(r"(_at|^as_of)$")


def _timestamps(value: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        return [item for key, child in value.items() for item in _timestamps(child, f"{path}.{key}" if path else key)]
    if isinstance(value, list):
        return [item for index, child in enumerate(value) for item in _timestamps(child, f"{path}[{index}]")]
    key = path.rsplit(".", 1)[-1]
    if isinstance(value, str) and _TIME_KEY.search(key) and parse_ref(value) is None:
        return [(path, value)]
    return []


def _check_offsets(node: PlanNode, tz: ZoneInfo, issues: list[PlanIssue]) -> None:
    """A timestamp's offset must be the café's real offset at that moment (DST-safe)."""
    for path, text in _timestamps(node.args):
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            continue  # schema validation reports malformed timestamps
        if moment.tzinfo is None:
            continue
        expected = moment.astimezone(tz).utcoffset()
        if moment.utcoffset() != expected:
            hours = expected.total_seconds() / 3600
            correct = moment.replace(tzinfo=None).isoformat() + f"{'+' if hours >= 0 else '-'}{abs(int(hours)):02d}:00"
            issues.append(
                PlanIssue(
                    "wrong_time_offset",
                    f"{path}={text} uses the wrong UTC offset for {moment.date()} in {tz.key}; "
                    f"local {moment.strftime('%H:%M')} that day is {correct}",
                    node.id,
                )
            )


def _check_order_quantities(node: PlanNode, by_id: dict[str, PlanNode], issues: list[PlanIssue]) -> None:
    if node.action != "propose_supplier_order":
        return
    ref = parse_ref(node.args.get("lines"))
    source = by_id.get(ref[0]) if ref else None
    if not ref or ref[1] != ".order_lines" or source is None or source.action != "calculate_shortfall":
        issues.append(
            PlanIssue(
                "unverified_quantity",
                'order quantities must come from code: set lines to "$<calculate_shortfall step>.order_lines"',
                node.id,
            )
        )


_SCENARIO_PATH = re.compile(r"^\.scenarios\.(low|typical|high)\.items$")


def _check_demand_source(node: PlanNode, by_id: dict[str, PlanNode], issues: list[PlanIssue]) -> None:
    """When the plan reads demand from history, the order math must use those numbers."""
    if node.action != "calculate_shortfall":
        return
    demand_steps = [n for n in by_id.values() if n.action == "estimate_event_demand"]
    if not demand_steps:
        return
    ref = parse_ref(node.args.get("items"))
    source = by_id.get(ref[0]) if ref else None
    if not ref or source is None or source.action != "estimate_event_demand" or not _SCENARIO_PATH.match(ref[1]):
        issues.append(
            PlanIssue(
                "unverified_demand",
                'demand must come from history: set items to "$<estimate_event_demand step>.scenarios.typical.items"',
                node.id,
            )
        )


def _window(args: dict[str, Any]) -> tuple[datetime, datetime] | None:
    start, end = args.get("start_at"), args.get("end_at")
    if not isinstance(start, str) or not isinstance(end, str) or parse_ref(start) or parse_ref(end):
        return None
    try:
        return datetime.fromisoformat(start), datetime.fromisoformat(end)
    except ValueError:
        return None


def _check_shift_evidence(node: PlanNode, nodes: list[PlanNode], issues: list[PlanIssue]) -> None:
    """Coverage and shift checks must look at the same time as the shift being added.

    A Friday coverage report is not evidence for a Sunday shift."""
    if node.action != "schedule_shift":
        return
    shift = _window(node.args)
    if shift is None:
        return
    for action, label in (("coverage_report", "staff coverage"), ("list_shifts", "existing shifts")):
        windows = [_window(other.args) for other in nodes if other.action == action]
        literal = [w for w in windows if w is not None]
        if not literal or len(literal) < len(windows):
            continue  # missing checks are reported elsewhere; $refs cannot be checked here
        if not any(start < shift[1] and end > shift[0] for start, end in literal):
            issues.append(
                PlanIssue(
                    "unrelated_prerequisite",
                    f"the {label} check does not cover the shift time {shift[0].isoformat()} to "
                    f"{shift[1].isoformat()}; check the same day and hours",
                    node.id,
                )
            )


def _check_args(node: PlanNode, issues: list[PlanIssue]) -> None:
    action = REGISTRY[node.action]
    model = action.input_model
    unknown = set(node.args) - set(model.model_fields)
    if unknown:
        issues.append(
            PlanIssue("invalid_args", f"unknown argument(s) {sorted(unknown)} for {node.action}", node.id)
        )
        return
    if find_refs(node.args):
        # Values arriving from earlier steps are checked at execution time; check the rest now.
        missing = [
            name
            for name, info in model.model_fields.items()
            if info.is_required() and name not in node.args
        ]
        if missing:
            issues.append(
                PlanIssue("invalid_args", f"missing required argument(s) {missing}", node.id)
            )
        return
    try:
        model.model_validate(node.args)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or '(args)'}: {e['msg']}" for e in error.errors()[:3]
        )
        issues.append(PlanIssue("invalid_args", f"{node.action}: {details}", node.id))


def validate_plan(
    plan: Plan,
    cafe_id: str,
    superset: SupersetDAG | None = None,
    timezone: str | None = None,
) -> ValidationResult:
    superset = superset or get_superset()
    tz = ZoneInfo(timezone) if timezone else None
    if plan.status != "plan":
        return ValidationResult(
            valid=False,
            issues=[PlanIssue("not_a_plan", f"planner returned {plan.status}")],
        )

    issues: list[PlanIssue] = []
    warnings: list[str] = []
    by_id = {node.id: node for node in plan.nodes}

    known = [node for node in plan.nodes if node.action in REGISTRY]
    for node in plan.nodes:
        if node.action not in REGISTRY:
            issues.append(PlanIssue("unknown_action", f"{node.action} is not a registered action", node.id))

    for node in known:
        _check_args(node, issues)
        _check_order_quantities(node, by_id, issues)
        _check_shift_evidence(node, known, issues)
        _check_demand_source(node, by_id, issues)
        if tz is not None:
            _check_offsets(node, tz, issues)
        node_cafe = node.args.get("cafe_id", cafe_id)
        if parse_ref(node_cafe) is None and node_cafe != cafe_id:
            issues.append(
                PlanIssue("cafe_mismatch", f"plan is for {cafe_id} but step uses {node_cafe}", node.id)
            )

    edges: dict[tuple[str, str], Edge] = {}

    # Data edges from $references.
    for node in known:
        for ref_id, path in find_refs(node.args):
            if ref_id == node.id:
                issues.append(PlanIssue("self_ref", f"${ref_id}{path} refers to itself", node.id))
                continue
            upstream = by_id.get(ref_id)
            if upstream is None:
                issues.append(PlanIssue("unknown_ref", f"${ref_id}{path} refers to no step", node.id))
                continue
            if upstream.action not in REGISTRY:
                continue
            if (upstream.action, node.action) not in superset.allowed_edges:
                issues.append(
                    PlanIssue(
                        "disallowed_dependency",
                        f"{node.action} cannot take input from {upstream.action}",
                        node.id,
                    )
                )
                continue
            edges[(ref_id, node.id)] = Edge(ref_id, node.id, "data")

    # Safety edges from the superset: every requires-group must be present in the plan.
    for node in known:
        for group in REGISTRY[node.action].requires:
            providers = [other.id for other in known if other.action in group and other.id != node.id]
            if not providers:
                issues.append(
                    PlanIssue(
                        "missing_prerequisite",
                        f"{node.action} must come after {' or '.join(group)}, which the plan does not include",
                        node.id,
                    )
                )
                continue
            for provider in providers:
                edges.setdefault((provider, node.id), Edge(provider, node.id, "safety"))

    seen: dict[tuple[str, str], str] = {}
    for node in known:
        key = (node.action, repr(sorted(node.args.items())))
        if key in seen:
            warnings.append(f"{node.id} repeats {seen[key]} with the same arguments")
        else:
            seen[key] = node.id

    # Cycles and levels.
    levels: list[list[str]] = []
    sorter: TopologicalSorter[str] = TopologicalSorter()
    for node in known:
        sorter.add(node.id)
    for edge in edges.values():
        sorter.add(edge.target, edge.source)
    try:
        sorter.prepare()
        while sorter.is_active():
            ready = sorted(sorter.get_ready())
            levels.append(ready)
            sorter.done(*ready)
    except CycleError as error:
        issues.append(PlanIssue("cycle", f"steps depend on each other in a loop: {error.args[1]}"))
        levels = []

    level_of = {node_id: index for index, ids in enumerate(levels) for node_id in ids}
    validated = [
        ValidatedNode(
            id=node.id,
            action=node.action,
            args=node.args,
            reason=node.reason,
            only_if=node.only_if,
            side_effect=REGISTRY[node.action].side_effect,
            requires_approval=REGISTRY[node.action].approval == "owner",
            depends_on=sorted(e.source for e in edges.values() if e.target == node.id),
            level=level_of.get(node.id, -1),
        )
        for node in known
    ]
    return ValidationResult(
        valid=not issues,
        issues=issues,
        warnings=warnings,
        nodes=validated,
        edges=sorted(edges.values(), key=lambda e: (e.source, e.target)),
        levels=levels,
    )


def format_issues_for_model(issues: list[PlanIssue]) -> str:
    return "\n".join(
        f"- [{issue.code}]{' step ' + issue.node_id if issue.node_id else ''}: {issue.message}"
        for issue in issues
    )
