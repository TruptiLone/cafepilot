"""ai_calls: one row per model request, through one wrapper, with no prompts or keys in it."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.config import get_settings
from backend.db.session import engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.agent import planner as planner_module
from backend.modules.agent.ai_calls import summarize
from backend.modules.agent.models import AICall
from backend.modules.agent.planner import SYSTEM_PROMPT
from backend.modules.agent.router import get_chat_client, get_planner_client

C = "CAFE001"
PLAN = {"status": "plan", "goal": "Who works here", "assumptions": [],
        "nodes": [{"id": "staff", "action": "list_employees", "args": {"cafe_id": C}, "reason": "who works"}]}


class Scripted:
    """A model client that answers from a list (an Exception is raised); records every call."""

    def __init__(self, *answers) -> None:
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._answers = list(answers)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        answer = self._answers[min(len(self.calls), len(self._answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        content = answer if isinstance(answer, str) else json.dumps(answer)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=None)


@pytest.fixture
def session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    db = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        if db.get(Cafe, C) is None:
            pytest.skip("demo data not seeded; run scripts/seed_all.sh")
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def no_log_file(tmp_path, monkeypatch):
    monkeypatch.setattr(planner_module, "LLM_LOG_PATH", tmp_path / "llm.jsonl")


def rows(ai_call_rows) -> list[AICall]:
    with ai_call_rows() as s:
        return list(s.scalars(select(AICall).order_by(AICall.created_at)))


def plan(session: Session, client, query: str = "who works here?") -> TestClient:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_planner_client] = lambda: client
    try:
        return TestClient(app).post("/api/v1/agent/runs", json={"query": query, "cafe_id": C})
    finally:
        app.dependency_overrides.clear()


def test_a_plan_is_one_row_linked_to_its_run(session: Session, ai_call_rows) -> None:
    response = plan(session, Scripted(PLAN))
    run_id = response.json()["run"]["run_id"]
    [row] = rows(ai_call_rows)
    assert (row.purpose, row.cafe_id, row.attempts, row.outcome, row.run_id) == ("plan", C, 1, "ok", run_id)
    assert row.model == get_settings().planner_model and row.latency_ms >= 0
    assert row.request_summary == "who works here?" and row.error is None


def test_a_second_attempt_is_one_row_marked_retried(session: Session, ai_call_rows) -> None:
    plan(session, Scripted("not json", PLAN))
    [row] = rows(ai_call_rows)
    assert (row.attempts, row.outcome) == (2, "retried")


def test_failures_and_timeouts_are_recorded(session: Session, ai_call_rows) -> None:
    assert plan(session, Scripted("not json")).status_code == 502
    assert plan(session, Scripted(type("APITimeoutError", (Exception,), {})("t"))).status_code == 502
    failed, timed_out = rows(ai_call_rows)
    assert (failed.outcome, failed.attempts) == ("failed", 2)
    assert (timed_out.outcome, timed_out.attempts) == ("timeout", 1)
    assert timed_out.error and "took too long" in timed_out.error


def test_chat_records_its_routing_and_reply_calls(session: Session, ai_call_rows) -> None:
    client = Scripted({"intent": "answer", "actions": [{"action": "list_employees", "args": {}}]}, "Here's the team.")
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_chat_client] = lambda: client
    try:
        TestClient(app).post("/api/v1/agent/chat", json={"message": "who works here", "channel": "text"})
    finally:
        app.dependency_overrides.clear()
    routing, reply = rows(ai_call_rows)
    assert [(r.purpose, r.outcome, r.request_summary) for r in (routing, reply)] == [
        ("chat", "ok", "who works here"), ("chat", "ok", "reply: who works here")]


def test_no_key_or_full_prompt_ends_up_in_a_row(session: Session, ai_call_rows, monkeypatch) -> None:
    secret = "sk-test-SECRET-KEY-1234567890"
    monkeypatch.setattr(get_settings(), "model_api_key", SecretStr(secret))
    query = ("Prepare for the market " * 20) + f" and use key {secret} with Bearer abc.def.ghi"
    plan(session, Scripted(PLAN), query)
    [row] = rows(ai_call_rows)
    stored = " ".join(str(getattr(row, c.name)) for c in AICall.__table__.columns)
    assert secret not in stored and "Bearer abc" not in stored
    assert SYSTEM_PROMPT[:60] not in stored and "reference_data" not in stored
    assert len(row.request_summary) <= 120


def test_summaries_hide_key_like_text() -> None:
    assert summarize("use sk-live-abcdef123456 now") == "use [hidden] now"
    assert summarize("x" * 40) == "[hidden]"  # one long token reads like a key
    assert summarize("word " * 60).endswith("…") and len(summarize("word " * 60)) == 120


def test_ai_calls_endpoint_lists_newest_first(session: Session, ai_call_rows) -> None:
    now = datetime.now(timezone.utc)
    with ai_call_rows() as s:
        for i, purpose in enumerate(("plan", "chat", "replan")):
            s.add(AICall(id=f"AIC-T{i}", cafe_id=C, created_at=now + timedelta(seconds=i), purpose=purpose,
                         model="muse-wonder-1.0", latency_ms=1000 * i, attempts=1, outcome="ok", run_id=None,
                         request_summary=f"q{i}", error=None))
        s.add(AICall(id="AIC-OTHER", cafe_id="ANOTHER_CAFE", created_at=now + timedelta(seconds=9), purpose="chat",
                     model="m", latency_ms=1, attempts=1, outcome="ok", run_id=None, request_summary="x", error=None))
        s.commit()
    app.dependency_overrides[get_db] = lambda: ai_call_rows()
    try:
        api = TestClient(app)
        listed = api.get("/api/v1/agent/ai-calls").json()
        two = api.get("/api/v1/agent/ai-calls?limit=2").json()
    finally:
        app.dependency_overrides.clear()
    assert [c["purpose"] for c in listed] == ["replan", "chat", "plan"]  # this café only, newest first
    assert [c["id"] for c in two] == ["AIC-T2", "AIC-T1"]
    assert set(listed[0]) == {"id", "cafe_id", "created_at", "purpose", "model", "latency_ms", "attempts", "outcome",
                              "run_id", "request_summary", "error"}
