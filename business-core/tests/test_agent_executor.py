import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.modules.agent import planner as planner_module
from backend.modules.agent.executor import (
    ApprovalStateError,
    create_run,
    decide,
    decide_all,
    execute,
    resolve_args,
    resolve_path,
)
from backend.modules.agent.models import AgentApproval
from backend.modules.agent.planner import Plan
from backend.modules.agent.router import get_planner_client
from backend.modules.agent.validator import validate_plan
from backend.modules.inventory.models import StockCount
from backend.modules.staffing.models import Employee, Shift
from scripts.seed_sales_catalog import catalog_identifier
from tests.sales_v2_support import seed_sales_v2_test_catalog

CAFE = "TEST_AGENT_EXECUTOR_CAFE"
SUNDAY = "2026-11-01"
AS_OF = datetime(2026, 10, 30, 1, tzinfo=timezone.utc)
COUNTED_AT = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()  # counts cannot be in the future


def _id(local: str) -> str:
    return catalog_identifier(CAFE, local)


@pytest.fixture
def session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    db = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        seed_sales_v2_test_catalog(db, CAFE)
        created = datetime(2026, 10, 1, tzinfo=timezone.utc)
        db.add_all(
            [
                Employee(employee_id="TEX_LEAD", cafe_id=CAFE, name="Alex", role="shift_lead", active=True, created_at=created),
                Employee(employee_id="TEX_NOOR", cafe_id=CAFE, name="Noor", role="barista", active=True, created_at=created),
            ]
        )
        db.flush()
        db.add(
            Shift(
                shift_id="TEX_SHIFT_LEAD",
                cafe_id=CAFE,
                employee_id="TEX_LEAD",
                start_at=datetime.fromisoformat(f"{SUNDAY}T07:00:00-08:00"),
                end_at=datetime.fromisoformat(f"{SUNDAY}T15:00:00-08:00"),
                status="scheduled",
                created_at=created,
            )
        )
        db.flush()
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def _node(node_id: str, action: str, args: dict, **extra) -> dict:
    return {"id": node_id, "action": action, "args": {"cafe_id": CAFE, **args}, "reason": f"{action} step", **extra}


def _start(session: Session, nodes: list[dict], query: str = "test") -> str:
    plan = Plan.model_validate({"status": "plan", "goal": "test goal", "assumptions": ["a1"], "nodes": nodes})
    validation = validate_plan(plan, CAFE)
    assert validation.valid, validation.issues
    run = create_run(
        session, query=query, cafe_id=CAFE, as_of=AS_OF, plan=plan,
        validation=validation, model="test-model", attempts=1,
    )
    return execute(session, run.run_id).run_id


def _staffing_plan() -> list[dict]:
    window = {"start_at": f"{SUNDAY}T08:00:00-08:00", "end_at": f"{SUNDAY}T14:00:00-08:00"}
    return [
        _node("coverage", "coverage_report", {**window, "required_staff": 2}),
        _node("staff", "list_employees", {}),
        _node("shifts", "list_shifts", window),
        _node(
            "add_barista",
            "schedule_shift",
            {"employee_id": "TEX_NOOR", "start_at": f"{SUNDAY}T07:30:00-08:00", "end_at": f"{SUNDAY}T14:30:00-08:00"},
            only_if="coverage shows fewer than 2 people",
        ),
    ]


def _shift_count(session: Session) -> int:
    return session.scalar(
        select(func.count()).select_from(Shift).where(Shift.cafe_id == CAFE, Shift.employee_id == "TEX_NOOR")
    )


def _nodes(session: Session, run_id: str) -> dict:
    from backend.modules.agent.executor import get_run

    return {n.node_id: n for n in get_run(session, run_id).nodes}


# ---------- reference resolution ---------------------------------------------


def test_resolve_path_and_args() -> None:
    results = {"demand": {"ingredients": [{"ingredient_id": "OAT", "quantity": "4800.000"}]}}
    assert resolve_path(results["demand"], ".ingredients[0].quantity") == "4800.000"
    resolved = resolve_args({"lines": [{"ingredient_id": "$demand.ingredients[0].ingredient_id"}], "n": 1}, results)
    assert resolved == {"lines": [{"ingredient_id": "OAT"}], "n": 1}
    with pytest.raises(IndexError):
        resolve_path(results["demand"], ".ingredients[3]")
    with pytest.raises(KeyError):
        resolve_args("$missing", results)


# ---------- reads run, writes pause -------------------------------------------


def test_read_only_plan_completes_with_results(session: Session) -> None:
    run_id = _start(
        session,
        [
            _node("stock", "get_stock_estimates", {"as_of": "2026-10-29T18:00:00-07:00"}),
            _node(
                "demand",
                "project_recipe_usage",
                {"target_at": f"{SUNDAY}T08:00:00-08:00", "items": [{"variation_id": _id("COF003_REG"), "quantity": 2}]},
            ),
        ],
    )
    nodes = _nodes(session, run_id)
    assert {n.status for n in nodes.values()} == {"done"}
    assert nodes["demand"].result["projection_status"] == "computed"
    json.dumps(nodes["stock"].result)
    from backend.modules.agent.executor import get_run

    assert get_run(session, run_id).status == "completed"


def test_write_waits_for_approval_and_nothing_is_written(session: Session) -> None:
    run_id = _start(session, _staffing_plan())
    nodes = _nodes(session, run_id)
    assert all(nodes[n].status == "done" for n in ("coverage", "staff", "shifts"))
    shift = nodes["add_barista"]
    assert shift.status == "awaiting_approval"
    assert shift.resolved_args["employee_id"] == "TEX_NOOR"
    assert shift.idempotency_key and shift.result is None
    assert _shift_count(session) == 0
    from backend.modules.agent.executor import get_run

    assert get_run(session, run_id).status == "awaiting_approval"


def test_approval_runs_the_write_exactly_once(session: Session) -> None:
    run_id = _start(session, _staffing_plan())
    run = decide(session, run_id, "add_barista", "approved", channel="voice")
    shift = next(n for n in run.nodes if n.node_id == "add_barista")
    assert shift.status == "done" and shift.result["employee_id"] == "TEX_NOOR"
    assert run.status == "completed"
    assert _shift_count(session) == 1

    decide(session, run_id, "add_barista", "approved")  # double click
    assert _shift_count(session) == 1
    approvals = session.scalars(select(AgentApproval).where(AgentApproval.run_id == run_id)).all()
    assert [(a.decision, a.channel) for a in approvals] == [("approved", "voice")]


def test_execute_twice_does_no_new_work(session: Session) -> None:
    run_id = _start(session, _staffing_plan())
    decide(session, run_id, "add_barista", "approved")
    before = {k: (v.status, v.finished_at) for k, v in _nodes(session, run_id).items()}
    execute(session, run_id)
    assert {k: (v.status, v.finished_at) for k, v in _nodes(session, run_id).items()} == before
    assert _shift_count(session) == 1


def test_replaying_the_same_plan_never_duplicates_a_write(session: Session) -> None:
    first = _start(session, _staffing_plan())
    decide(session, first, "add_barista", "approved")
    second = _start(session, _staffing_plan())
    replay = _nodes(session, second)["add_barista"]
    assert replay.status == "done"
    assert replay.replay_of == f"{first}/add_barista"
    assert _shift_count(session) == 1


def test_rejection_skips_the_write_and_its_dependents(session: Session) -> None:
    run_id = _start(
        session,
        [
            _node("count", "create_stock_count", {"items": [{"ingredient_id": _id("OAT_MILK"), "counted_quantity": "9000"}]}),
            _node("verify", "verify_stock_count", {"count_id": "$count.count_id", "counted_at": COUNTED_AT}),
        ],
    )
    nodes = _nodes(session, run_id)
    assert nodes["count"].status == "awaiting_approval"
    assert nodes["verify"].status == "pending"  # waits on its upstream write

    run = decide(session, run_id, "count", "rejected", note="I'll count tomorrow")
    by_id = {n.node_id: n for n in run.nodes}
    assert by_id["count"].status == "rejected"
    assert by_id["verify"].status == "skipped" and "count" in by_id["verify"].error
    assert run.status == "completed_with_issues"
    assert session.scalar(select(func.count()).select_from(StockCount).where(StockCount.cafe_id == CAFE)) == 0


def test_reference_is_resolved_from_an_approved_write(session: Session) -> None:
    run_id = _start(
        session,
        [
            _node("count", "create_stock_count", {"items": [{"ingredient_id": _id("OAT_MILK"), "counted_quantity": "9000"}]}),
            _node("verify", "verify_stock_count", {"count_id": "$count.count_id", "counted_at": COUNTED_AT}),
        ],
    )
    decide(session, run_id, "count", "approved")
    nodes = _nodes(session, run_id)
    assert nodes["verify"].status == "awaiting_approval"
    assert nodes["verify"].resolved_args["count_id"] == nodes["count"].result["count_id"]
    run = decide_all(session, run_id, "approved")
    assert run.status == "completed"
    count = session.get(StockCount, nodes["count"].result["count_id"])
    assert count.status == "verified"


def test_failed_step_is_recorded_and_dependents_skipped(session: Session) -> None:
    run_id = _start(
        session,
        [
            _node("count", "get_stock_count", {"count_id": "NO_SUCH_COUNT"}),
            _node("verify", "verify_stock_count", {"count_id": "$count.count_id"}),
        ],
    )
    nodes = _nodes(session, run_id)
    assert nodes["count"].status == "failed" and "NotFound" in nodes["count"].error
    assert nodes["verify"].status == "skipped"


def test_bad_reference_fails_the_step_not_the_run(session: Session) -> None:
    run_id = _start(
        session,
        [
            _node("count", "create_stock_count", {"items": [{"ingredient_id": _id("OAT_MILK"), "counted_quantity": "1"}]}),
            _node("verify", "verify_stock_count", {"count_id": "$count.no_such_field"}),
        ],
    )
    decide(session, run_id, "count", "approved")
    verify = _nodes(session, run_id)["verify"]
    assert verify.status == "failed" and "resolve" in verify.error


def test_cannot_decide_reads_or_steps_not_yet_waiting(session: Session) -> None:
    run_id = _start(
        session,
        [
            _node("count", "create_stock_count", {"items": [{"ingredient_id": _id("OAT_MILK"), "counted_quantity": "1"}]}),
            _node("verify", "verify_stock_count", {"count_id": "$count.count_id"}),
        ],
    )
    with pytest.raises(ApprovalStateError, match="not waiting"):
        decide(session, run_id, "verify", "approved")
    read_run = _start(session, [_node("staff", "list_employees", {})])
    with pytest.raises(ApprovalStateError, match="read step"):
        decide(session, read_run, "staff", "approved")


# ---------- API ----------------------------------------------------------------


class FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **_):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))], usage=None)


@pytest.fixture
def api(session: Session, tmp_path, monkeypatch):
    monkeypatch.setattr(planner_module, "LLM_LOG_PATH", tmp_path / "log.jsonl")

    def make(plan: dict) -> TestClient:
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_planner_client] = lambda: FakeClient(json.dumps(plan))
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


def _plan_payload(nodes: list[dict]) -> dict:
    return {"status": "plan", "goal": "Cover Sunday", "assumptions": [], "nodes": nodes}


def test_api_full_cycle_plan_run_approve(api, session: Session) -> None:
    http = api(_plan_payload(_staffing_plan()))
    started = http.post("/api/v1/agent/runs", json={"query": "cover sunday", "cafe_id": CAFE})
    assert started.status_code == 200
    run = started.json()["run"]
    assert run["status"] == "awaiting_approval"
    assert run["levels"] == [["coverage", "shifts", "staff"], ["add_barista"]]

    listing = http.get("/api/v1/agent/runs", params={"cafe_id": CAFE}).json()
    assert listing[0]["run_id"] == run["run_id"] and listing[0]["awaiting_approval"] == 1

    approved = http.post(
        f"/api/v1/agent/runs/{run['run_id']}/nodes/add_barista/decision", json={"decision": "approved"}
    ).json()
    assert approved["status"] == "completed"
    assert http.get(f"/api/v1/agent/runs/{run['run_id']}").json()["nodes"][-1]["status"] == "done"
    assert _shift_count(session) == 1


def test_api_invalid_plan_returns_reasons_and_no_run(api) -> None:
    nodes = [n for n in _staffing_plan() if n["id"] != "coverage"]
    http = api(_plan_payload(nodes))
    body = http.post("/api/v1/agent/runs", json={"query": "x", "cafe_id": CAFE}).json()
    assert body["run"] is None
    assert body["planning"]["validation"]["issues"][0]["code"] == "missing_prerequisite"


def test_api_errors(api) -> None:
    http = api(_plan_payload(_staffing_plan()))
    assert http.get("/api/v1/agent/runs/RUN-NOPE").status_code == 404
    run_id = http.post("/api/v1/agent/runs", json={"query": "x", "cafe_id": CAFE}).json()["run"]["run_id"]
    assert http.post(f"/api/v1/agent/runs/{run_id}/nodes/nope/decision", json={"decision": "approved"}).status_code == 404
    assert http.post(f"/api/v1/agent/runs/{run_id}/nodes/staff/decision", json={"decision": "approved"}).status_code == 409
    assert http.post(f"/api/v1/agent/runs/{run_id}/nodes/add_barista/decision", json={"decision": "maybe"}).status_code == 422


def test_api_approve_all(api, session: Session) -> None:
    http = api(_plan_payload(_staffing_plan()))
    run_id = http.post("/api/v1/agent/runs", json={"query": "x", "cafe_id": CAFE}).json()["run"]["run_id"]
    body = http.post(f"/api/v1/agent/runs/{run_id}/decision", json={"decision": "approved"}).json()
    assert body["status"] == "completed" and _shift_count(session) == 1


# ---------- retry safety (review findings) ------------------------------------


def test_write_interrupted_after_saving_is_adopted_not_repeated(session: Session) -> None:
    """Server dies after the shift is saved but before the step is marked done."""
    from backend.modules.agent.executor import record_id_for
    from backend.modules.agent.registry import REGISTRY

    run_id = _start(session, _staffing_plan())
    node = _nodes(session, run_id)["add_barista"]
    session.add(AgentApproval(approval_id="APR-TEST", run_id=run_id, node_id="add_barista", decision="approved",
                              decided_by="owner", channel="web", decided_at=datetime.now(timezone.utc)))
    # the write happens, then the process "crashes" leaving the step in 'running'
    REGISTRY["schedule_shift"].run(session, node.resolved_args, record_id=record_id_for(node))
    node.status = "running"
    session.commit()
    assert _shift_count(session) == 1

    run = execute(session, run_id)  # resumed by any later call
    node = _nodes(session, run_id)["add_barista"]
    assert node.status == "done" and node.result["shift_id"] == record_id_for(node)
    assert _shift_count(session) == 1
    assert run.status == "completed"


def test_retried_write_uses_the_same_record_id(session: Session) -> None:
    from backend.modules.agent.executor import record_id_for

    run_id = _start(session, _staffing_plan())
    expected = record_id_for(_nodes(session, run_id)["add_barista"])
    decide(session, run_id, "add_barista", "approved")
    node = _nodes(session, run_id)["add_barista"]
    assert node.result["shift_id"] == expected and expected.startswith("shift_agent_")
    # a second attempt with the same id cannot create a second shift
    from backend.modules.agent.registry import REGISTRY

    with pytest.raises(Exception):
        REGISTRY["schedule_shift"].run(session, node.resolved_args, record_id=expected)
    session.rollback()
    assert _shift_count(session) == 1


def test_write_without_recorded_approval_is_refused(session: Session) -> None:
    run_id = _start(session, _staffing_plan())
    node = _nodes(session, run_id)["add_barista"]
    node.status = "approved"  # status forged without an approval record
    session.commit()
    execute(session, run_id)
    node = _nodes(session, run_id)["add_barista"]
    assert node.status == "failed" and "no recorded owner approval" in node.error
    assert _shift_count(session) == 0


def test_cafe_always_comes_from_the_run(session: Session) -> None:
    """Even if a stored step names another café, the executor uses the run's café."""
    plan = Plan.model_validate({"status": "plan", "goal": "g", "nodes": _staffing_plan()})
    run = create_run(session, query="q", cafe_id=CAFE, as_of=AS_OF, plan=plan,
                     validation=validate_plan(plan, CAFE), model="m", attempts=1)
    staff = next(n for n in run.nodes if n.node_id == "staff")
    staff.args = {**staff.args, "cafe_id": "SOME_OTHER_CAFE"}
    session.commit()
    execute(session, run.run_id)
    staff = _nodes(session, run.run_id)["staff"]
    assert staff.status == "done" and staff.resolved_args["cafe_id"] == CAFE
    assert {e["employee_id"] for e in staff.result} >= {"TEX_NOOR"}
