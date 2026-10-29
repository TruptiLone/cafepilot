import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.modules.agent import planner as planner_module
from backend.modules.agent.planner import (
    Plan,
    PlannerError,
    build_planning_context,
    parse_plan,
    plan_query,
)
from backend.modules.agent.registry import REGISTRY
from backend.modules.agent.router import get_planner_client
from backend.modules.staffing.models import Employee
from scripts.seed_sales_catalog import catalog_identifier
from tests.sales_v2_support import seed_sales_v2_test_catalog

TEST_CAFE_ID = "TEST_AGENT_PLANNER_CAFE"
FIXTURE = Path(__file__).parent / "fixtures" / "agent" / "hero_plan_mock.json"
HERO_QUERY = (
    "Farmers market this Sunday Nov 1, 8 AM-2 PM. Check milk and bean stock as of Thursday "
    "6 PM, check Sunday staff coverage, and add a barista 7:30 AM-2:30 PM if we're short."
)


class FakeClient:
    """Stands in for the OpenAI-compatible Meta client; records every call."""

    def __init__(self, content: str | None) -> None:
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._content = content

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))],
            usage=None,
        )


@pytest.fixture(autouse=True)
def no_log_files(tmp_path, monkeypatch):
    monkeypatch.setattr(planner_module, "LLM_LOG_PATH", tmp_path / "llm_calls.jsonl")
    return tmp_path / "llm_calls.jsonl"


@pytest.fixture
def database_session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        seed_sales_v2_test_catalog(session, TEST_CAFE_ID)
        session.add(
            Employee(
                employee_id="TEST_PLANNER_EMP_1",
                cafe_id=TEST_CAFE_ID,
                name="Noor",
                role="barista",
                active=True,
                created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            )
        )
        session.flush()
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _hero_json() -> str:
    return FIXTURE.read_text()


# ---------- plan schema ----------------------------------------------------


def test_fixture_plan_parses_and_uses_only_registered_actions() -> None:
    plan = parse_plan(_hero_json())
    assert plan.status == "plan"
    assert all(node.action in REGISTRY for node in plan.nodes)


def test_parse_accepts_code_fences() -> None:
    plan = parse_plan("```json\n" + _hero_json() + "\n```")
    assert len(plan.nodes) == 7


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not json", "invalid JSON"),
        (json.dumps({"status": "plan", "goal": "x", "nodes": []}), "malformed"),
        (json.dumps({"status": "needs_clarification", "goal": "x"}), "malformed"),
        (
            json.dumps(
                {
                    "status": "plan",
                    "goal": "x",
                    "nodes": [
                        {"id": "a", "action": "list_employees", "reason": "r"},
                        {"id": "a", "action": "list_products", "reason": "r"},
                    ],
                }
            ),
            "malformed",
        ),
        (
            json.dumps(
                {"status": "plan", "goal": "x", "nodes": [{"id": "Bad Id", "action": "x", "reason": "r"}]}
            ),
            "malformed",
        ),
    ],
)
def test_malformed_model_output_is_rejected(payload: str, message: str) -> None:
    with pytest.raises(PlannerError, match=message):
        parse_plan(payload)


def test_clarification_and_out_of_scope_have_no_nodes() -> None:
    clarify = Plan.model_validate(
        {"status": "needs_clarification", "goal": "Prep event", "question": "Which date?"}
    )
    assert clarify.nodes == []
    out = Plan.model_validate({"status": "out_of_scope", "goal": "Write a poem"})
    assert out.question is None


# ---------- context and prompt ---------------------------------------------


def test_context_contains_real_reference_ids(database_session: Session) -> None:
    context = build_planning_context(
        database_session, TEST_CAFE_ID, datetime(2026, 10, 30, 1, tzinfo=timezone.utc)
    )
    variation_ids = {row["variation_id"] for row in context.reference["menu_variations"]}
    modifier_ids = {row["modifier_id"] for row in context.reference["modifiers"]}
    assert catalog_identifier(TEST_CAFE_ID, "COF003_REG") in variation_ids
    assert catalog_identifier(TEST_CAFE_ID, "MILK_OAT") in modifier_ids
    assert {"employee_id": "TEST_PLANNER_EMP_1", "name": "Noor", "role": "barista"} in context.reference["employees"]
    assert context.as_of.utcoffset().total_seconds() == -7 * 3600  # café local, PDT


def test_unknown_cafe_is_rejected(database_session: Session) -> None:
    with pytest.raises(LookupError):
        build_planning_context(database_session, "NO_SUCH_CAFE")


def test_prompt_sends_registry_reference_and_query(database_session: Session, no_log_files) -> None:
    client = FakeClient(_hero_json())
    context = build_planning_context(database_session, TEST_CAFE_ID)
    result = plan_query(HERO_QUERY, context, client, model="test-model")

    call = client.calls[0]
    assert call["model"] == "test-model"
    assert call["temperature"] == 0
    user = json.loads(call["messages"][1]["content"])
    assert user["owner_request"] == HERO_QUERY
    assert [a["name"] for a in user["action_list"]] == list(REGISTRY)
    schedule = next(a for a in user["action_list"] if a["name"] == "schedule_shift")
    assert schedule["needs_owner_approval"] is True
    assert ["coverage_report"] in schedule["must_come_after"]
    assert "employees" in user["reference_data"]
    assert result.plan.goal.startswith("Prepare milk")

    logged = [json.loads(line) for line in no_log_files.read_text().splitlines()]
    assert logged[0]["purpose"] == "plan" and "error" not in logged[0]


def test_bad_model_output_is_logged_with_error(database_session: Session, no_log_files) -> None:
    context = build_planning_context(database_session, TEST_CAFE_ID)
    with pytest.raises(PlannerError):
        plan_query("anything", context, FakeClient("sorry, I can't"), model="m")
    with pytest.raises(PlannerError, match="no text"):
        plan_query("anything", context, FakeClient(None), model="m")
    lines = no_log_files.read_text().splitlines()
    assert len(lines) == 2 and all("error" in json.loads(line) for line in lines)


# ---------- API --------------------------------------------------------------


@pytest.fixture
def client_factory(database_session: Session):
    def make(content: str | None) -> tuple[TestClient, FakeClient]:
        fake = FakeClient(content)
        app.dependency_overrides[get_db] = lambda: database_session
        app.dependency_overrides[get_planner_client] = lambda: fake
        return TestClient(app), fake

    yield make
    app.dependency_overrides.clear()


def test_plan_endpoint_rejects_other_cafe_plan_after_one_retry(client_factory) -> None:
    http, fake = client_factory(_hero_json())
    response = http.post(
        "/api/v1/agent/plans",
        json={"query": HERO_QUERY, "cafe_id": TEST_CAFE_ID, "as_of": "2026-10-29T18:00:00-07:00"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["plan"]["status"] == "plan"
    assert len(body["plan"]["nodes"]) == 7
    assert body["as_of"].startswith("2026-10-29T18:00:00")
    # The fixture targets CAFE001, so the validator rejects it and the model gets one retry.
    assert body["validation"]["valid"] is False
    assert {i["code"] for i in body["validation"]["issues"]} == {"cafe_mismatch"}
    assert body["attempts"] == 2 and len(fake.calls) == 2
    feedback = fake.calls[1]["messages"][-1]["content"]
    assert "cafe_mismatch" in feedback and fake.calls[1]["messages"][-2]["role"] == "assistant"


def test_plan_endpoint_maps_model_failure_to_502(client_factory) -> None:
    http, _ = client_factory("not json")
    response = http.post("/api/v1/agent/plans", json={"query": "hi", "cafe_id": TEST_CAFE_ID})
    assert response.status_code == 502


def test_plan_endpoint_rejects_naive_as_of(client_factory) -> None:
    http, fake = client_factory(_hero_json())
    response = http.post(
        "/api/v1/agent/plans",
        json={"query": "x", "cafe_id": TEST_CAFE_ID, "as_of": "2026-10-29T18:00:00"},
    )
    assert response.status_code == 422
    assert fake.calls == []


def test_plan_endpoint_unknown_cafe_404_before_model_call(client_factory) -> None:
    http, fake = client_factory(_hero_json())
    response = http.post("/api/v1/agent/plans", json={"query": "x", "cafe_id": "NO_SUCH_CAFE"})
    assert response.status_code == 404
    assert fake.calls == []


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_api_key_gives_503(monkeypatch, database_session: Session, key) -> None:
    from pydantic import SecretStr

    from backend.db import config

    monkeypatch.setattr(
        config.get_settings(), "model_api_key", None if key is None else SecretStr(key), raising=False
    )
    app.dependency_overrides[get_db] = lambda: database_session
    try:
        response = TestClient(app).post(
            "/api/v1/agent/plans", json={"query": "x", "cafe_id": TEST_CAFE_ID}
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert "MODEL_API_KEY" in response.json()["detail"]
