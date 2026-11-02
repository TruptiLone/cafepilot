import json
from datetime import datetime
from types import SimpleNamespace

from openai.types.chat import ChatCompletion

from backend.modules.agent.planner import PlanningContext
from scripts.compare_planners import MODELS, SETTINGS, compare_pair, digest, run_case, write_reports


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def response(plan, *, finish_reason="stop"):
    return ChatCompletion.model_validate({
        "id": "test-response", "created": 0, "model": "test-model", "object": "chat.completion",
        "choices": [{"index": 0, "finish_reason": finish_reason,
                     "message": {"role": "assistant", "content": json.dumps(plan)}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80},
    })


def plan(action="list_ingredients", node_id="ingredients"):
    return {"status": "plan", "goal": "Inspect ingredients", "assumptions": [], "question": None,
            "nodes": [{"id": node_id, "action": action, "args": {"cafe_id": "CAFE001"},
                       "reason": "Read the actual ingredient catalog", "only_if": None}]}


CONTEXT = PlanningContext("CAFE001", "America/Los_Angeles",
                          datetime.fromisoformat("2026-10-29T18:00:00-07:00"),
                          datetime.fromisoformat("2026-10-29T18:00:00-07:00"), {})
MESSAGES = [{"role": "system", "content": "Use registered actions."},
            {"role": "user", "content": "Read ingredients."}]


def client_with(clock, outputs):
    calls = []
    outputs = iter(outputs)

    def create(**kwargs):
        calls.append(kwargs)
        elapsed, output = next(outputs)
        clock.now += elapsed
        if isinstance(output, Exception):
            raise output
        return output

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


def test_invalid_plan_gets_one_repair_and_separate_timings():
    clock = Clock()
    client, calls = client_with(clock, [(1.5, response(plan("invented_action"))), (2.5, response(plan()))])
    saved = []
    result = run_case(client, MODELS[0], MESSAGES, CONTEXT, clock=clock, save_attempt=saved.append)
    assert result["initial_api_ms"] == 1500
    assert result["total_api_ms"] == result["total_wall_ms"] == 4000
    assert [a["outcome"] for a in result["attempts"]] == ["invalid_plan", "valid_plan"]
    assert len(saved) == 2 and saved[0]["number"] == 1
    assert calls[1]["messages"][-2]["content"] == saved[0]["raw_content"]
    assert "unknown_action" in calls[1]["messages"][-1]["content"]
    assert all(c[k] == v for c in calls for k, v in SETTINGS.items())


def test_transport_failure_is_recorded_without_repair_or_secret_leak():
    clock = Clock()
    client, calls = client_with(clock, [(3, RuntimeError("request failed secret-key-value"))])
    result = run_case(client, MODELS[1], MESSAGES, CONTEXT, clock=clock, secret="secret-key-value")
    assert result["outcome"] == "api_error" and len(calls) == 1
    assert result["initial_api_ms"] == 3000
    assert "secret-key-value" not in json.dumps(result)


def test_clarification_is_preserved_without_treating_it_as_invalid_plan():
    clock = Clock()
    clarification = {"status": "needs_clarification", "goal": "Clarify event", "assumptions": [],
                     "question": "Which date and time is the private event?", "nodes": []}
    client, calls = client_with(clock, [(1, response(clarification))])
    result = run_case(client, MODELS[0], MESSAGES, CONTEXT, clock=clock)
    assert result["outcome"] == "needs_clarification" and len(calls) == 1
    assert result["final_validation"]["issues"][0]["code"] == "not_a_plan"


def test_truncation_is_explicit_and_at_most_one_correction_is_requested():
    clock = Clock()
    client, calls = client_with(clock, [(1, response(plan(), finish_reason="length")),
                                      (2, response(plan(), finish_reason="length"))])
    result = run_case(client, MODELS[0], MESSAGES, CONTEXT, clock=clock)
    assert result["outcome"] == "truncated" and len(calls) == 2
    assert result["total_api_ms"] == 3000


def test_both_models_receive_identical_initial_inputs_and_inputs_are_not_mutated():
    fingerprints = []
    for model in MODELS:
        clock = Clock()
        client, calls = client_with(clock, [(1, response(plan()))])
        result = run_case(client, model, MESSAGES, CONTEXT, clock=clock)
        fingerprints.append(result["initial_input_sha256"])
        assert calls[0]["messages"] == MESSAGES
    assert fingerprints[0] == fingerprints[1] == digest(MESSAGES)
    assert len(MESSAGES) == 2


def test_node_id_renaming_does_not_create_argument_differences_but_real_changes_do():
    a = {"final_plan": plan(node_id="first"), "initial_api_ms": 1000}
    b = {"final_plan": plan(node_id="renamed"), "initial_api_ms": 2000}
    a["final_plan"]["nodes"].append({"id": "order", "action": "propose_supplier_order",
                                     "args": {"lines": "$first.order_lines", "supplier_id": "BAY_DAIRY"}})
    b["final_plan"]["nodes"].append({"id": "different_order", "action": "propose_supplier_order",
                                     "args": {"lines": "$renamed.order_lines", "supplier_id": "BAY_DAIRY"}})
    assert compare_pair(a, b)["argument_differences"] == []
    b["final_plan"]["nodes"][1]["args"]["supplier_id"] = "WILLOW_RIDGE"
    diff = compare_pair(a, b)
    assert diff["argument_differences"][0]["action"] == "propose_supplier_order"
    assert diff["wonder_minus_spark_initial_ms"] == 1000


def test_report_escapes_model_and_query_text_and_preserves_raw_attempts(tmp_path):
    clock = Clock()
    p = plan()
    p["goal"] = "<script>alert('not executable')</script>"
    client, _ = client_with(clock, [(1, response(p))])
    row = run_case(client, MODELS[0], MESSAGES, CONTEXT, clock=clock)
    row["case_id"] = "Q01"
    write_reports(tmp_path, [{"id": "Q01", "title": "Test", "query": "<img src=x onerror=alert(1)>",
                              "review_checks": ["Read first"]}], [row],
                  {"frozen_time": "2026-10-29T18:00:00-07:00", "timeout_seconds": 120})
    html = (tmp_path / "report.html").read_text()
    assert "<script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html and "&lt;img" in html
    saved = json.loads((tmp_path / "results.json").read_text())
    assert saved["results"][0]["attempts"][0]["messages"] == MESSAGES
    assert saved["results"][0]["attempts"][0]["response"]["usage"]["total_tokens"] == 80
