import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.modules.agent import planner as planner_module
from backend.modules.agent.planner import Plan, PlanningContext, parse_plan
from backend.modules.agent.service import plan_and_validate
from backend.modules.agent.validator import find_refs, parse_ref, validate_plan

FIXTURES = Path(__file__).parent / "fixtures" / "agent"
CAFE = "CAFE001"


def _plan(name: str = "hero_plan_mock.json") -> dict:
    data = json.loads((FIXTURES / name).read_text())
    return data.get("plan", data)


def _validate(payload: dict):
    return validate_plan(Plan.model_validate(payload), CAFE)


def _codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def _node(payload: dict, node_id: str) -> dict:
    return next(n for n in payload["nodes"] if n["id"] == node_id)


# ---------- accepted plans ---------------------------------------------------


def test_mock_hero_plan_is_valid_with_reads_before_writes() -> None:
    result = _validate(_plan())
    assert result.valid, result.issues
    assert result.levels[0] == ["coverage", "demand", "shifts", "staff", "stock"]
    assert result.levels[1] == ["add_barista", "order"]
    writes = {n.id for n in result.nodes if n.requires_approval}
    assert writes == {"add_barista", "order"}


def test_safety_edges_are_added_even_without_refs() -> None:
    result = _validate(_plan())
    safety = {(e.source, e.target) for e in result.edges if e.kind == "safety"}
    assert {("coverage", "add_barista"), ("staff", "add_barista"), ("shifts", "add_barista")} <= safety
    assert ("stock", "order") in safety


def test_live_hero_plan_is_valid_if_present() -> None:
    path = FIXTURES / "hero_plan_live.json"
    if not path.exists():
        pytest.skip("live planner fixture not committed yet")
    result = _validate(_plan("hero_plan_live.json"))
    assert result.valid, result.issues
    last = result.levels[-1]
    assert all(n.requires_approval for n in result.nodes if n.id in last)


def test_data_edge_from_ref_is_kept_and_typed() -> None:
    payload = _plan()
    _node(payload, "order")["args"]["note"] = "$demand.ingredients[0].name"
    result = _validate(payload)
    assert result.valid, result.issues
    assert any(e.source == "demand" and e.target == "order" and e.kind == "data" for e in result.edges)


# ---------- rejected plans ---------------------------------------------------


def test_unknown_action_is_rejected() -> None:
    payload = _plan()
    payload["nodes"].append({"id": "sell", "action": "create_sale", "args": {}, "reason": "r"})
    assert "unknown_action" in _codes(_validate(payload))


def test_bad_argument_value_is_rejected() -> None:
    payload = _plan()
    _node(payload, "stock")["args"]["as_of"] = "2026-10-29T18:00:00"  # no offset
    result = _validate(payload)
    assert _codes(result) == {"invalid_args"}
    assert result.issues[0].node_id == "stock"


def test_unknown_argument_name_is_rejected() -> None:
    payload = _plan()
    _node(payload, "staff")["args"]["role"] = "barista"
    assert _codes(_validate(payload)) == {"invalid_args"}


def test_missing_required_argument_with_refs_is_rejected() -> None:
    payload = _plan()
    args = _node(payload, "add_barista")["args"]
    args["employee_id"] = "$staff[0].employee_id"
    del args["start_at"]
    assert "invalid_args" in _codes(_validate(payload))


def test_ref_to_missing_step_and_self_ref_are_rejected() -> None:
    payload = _plan()
    _node(payload, "order")["args"]["note"] = "$nowhere"
    _node(payload, "stock")["args"]["ingredient_id"] = "$stock.ingredient_id"
    assert {"unknown_ref", "self_ref"} <= _codes(_validate(payload))


def test_dependency_outside_superset_is_rejected() -> None:
    payload = _plan()
    _node(payload, "order")["args"]["note"] = "$staff[0].name"  # employees do not feed orders
    assert "disallowed_dependency" in _codes(_validate(payload))


def test_write_without_safety_prerequisite_is_rejected() -> None:
    payload = _plan()
    payload["nodes"] = [n for n in payload["nodes"] if n["id"] != "coverage"]
    result = _validate(payload)
    assert _codes(result) == {"missing_prerequisite"}
    assert "coverage_report" in result.issues[0].message


def test_any_of_prerequisite_group_accepts_either_action() -> None:
    payload = {
        "status": "plan",
        "goal": "verify",
        "nodes": [
            {"id": "count", "action": "get_stock_count", "args": {"count_id": "C1"}, "reason": "r"},
            {"id": "verify", "action": "verify_stock_count", "args": {"count_id": "C1"}, "reason": "r"},
        ],
    }
    assert _validate(payload).valid


def test_other_cafe_is_rejected() -> None:
    payload = _plan()
    _node(payload, "staff")["args"]["cafe_id"] = "CAFE999"
    assert _codes(_validate(payload)) == {"cafe_mismatch"}


def test_back_reference_is_blocked_by_the_superset() -> None:
    payload = _plan()
    _node(payload, "coverage")["args"]["cafe_id"] = "$add_barista.cafe_id"
    assert "disallowed_dependency" in _codes(_validate(payload))


def test_cycle_check_is_a_backstop_if_the_rulebook_ever_allowed_one() -> None:
    from backend.modules.agent.superset import SupersetDAG, get_superset

    real = get_superset()
    permissive = SupersetDAG(
        data_edges=real.data_edges | {("schedule_shift", "coverage_report")},
        safety_edges=real.safety_edges,
    )
    payload = _plan()
    _node(payload, "coverage")["args"]["cafe_id"] = "$add_barista.cafe_id"
    result = validate_plan(Plan.model_validate(payload), CAFE, permissive)
    assert _codes(result) == {"cycle"} and result.levels == []


def test_clarification_is_not_executable() -> None:
    result = validate_plan(
        Plan.model_validate({"status": "needs_clarification", "goal": "g", "question": "When?"}), CAFE
    )
    assert not result.valid and _codes(result) == {"not_a_plan"}


def test_duplicate_step_is_a_warning_not_an_error() -> None:
    payload = _plan()
    duplicate = deepcopy(_node(payload, "staff"))
    duplicate["id"] = "staff_again"
    payload["nodes"].append(duplicate)
    result = _validate(payload)
    assert result.valid and result.warnings


def test_ref_parsing() -> None:
    assert parse_ref("$stock") == ("stock", "")
    assert parse_ref("$demand.ingredients[0].quantity") == ("demand", ".ingredients[0].quantity")
    assert parse_ref("costs $5") is None
    assert find_refs({"a": ["$x", {"b": "$y.z"}], "c": 1}) == [("x", ""), ("y", ".z")]


# ---------- retry with feedback -----------------------------------------------


class ScriptedClient:
    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.responses.pop(0)))], usage=None
        )


@pytest.fixture
def context(tmp_path, monkeypatch) -> PlanningContext:
    from datetime import datetime, timezone

    monkeypatch.setattr(planner_module, "LLM_LOG_PATH", tmp_path / "log.jsonl")
    now = datetime(2026, 10, 30, 1, tzinfo=timezone.utc)
    return PlanningContext(CAFE, "America/Los_Angeles", now, now, {})


def test_rejected_plan_is_fixed_on_retry(context) -> None:
    broken = _plan()
    broken["nodes"] = [n for n in broken["nodes"] if n["id"] != "coverage"]
    client = ScriptedClient(json.dumps(broken), json.dumps(_plan()))
    outcome = plan_and_validate("q", context, client, model="m")
    assert outcome.validation.valid and outcome.attempts == 2
    assert "missing_prerequisite" in client.calls[1]["messages"][-1]["content"]


def test_unparseable_output_is_retried(context) -> None:
    client = ScriptedClient("oops", json.dumps(_plan()))
    outcome = plan_and_validate("q", context, client, model="m")
    assert outcome.validation.valid and outcome.attempts == 2
    assert client.calls[1]["messages"][-2] == {"role": "assistant", "content": "oops"}


def test_gives_up_after_two_attempts_and_reports_issues(context) -> None:
    broken = _plan()
    broken["nodes"] = [n for n in broken["nodes"] if n["id"] != "coverage"]
    client = ScriptedClient(json.dumps(broken), json.dumps(broken))
    outcome = plan_and_validate("q", context, client, model="m")
    assert not outcome.validation.valid and outcome.attempts == 2
    assert len(client.calls) == 2


def test_clarification_is_returned_without_retry(context) -> None:
    client = ScriptedClient(json.dumps({"status": "needs_clarification", "goal": "g", "question": "Which day?"}))
    outcome = plan_and_validate("q", context, client, model="m")
    assert outcome.attempts == 1 and outcome.result.plan.question == "Which day?"
