"""The public demo: access code, daily Muse limit, sample photos and "Start over"."""

import base64
import json
from datetime import datetime
from pathlib import Path
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
from backend.modules.agent.ai_calls import call_model
from backend.modules.agent.router import get_planner_client
from backend.modules.demo import access, muse_limit
from backend.modules.purchasing.models import Receipt
from backend.modules.receiving import service
from backend.modules.receiving.samples import SAMPLES
from scripts.seed_event_surge import apply_reset, plan_reset, seed_event_surge

C = "CAFE001"
LIVE = json.loads((Path(__file__).parent / "fixtures/receiving/vision_live.json").read_text())


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "demo_access_code", None)
    monkeypatch.setattr(settings, "muse_daily_limit", None)
    access.failures.clear()
    muse_limit.reset_for_tests()
    yield settings
    access.failures.clear()
    muse_limit.reset_for_tests()
    app.dependency_overrides.clear()


def _lock(settings, code="latte-art-42"):
    settings.demo_access_code = SecretStr(code)
    return code


# --- access code -------------------------------------------------------------------------------


def test_no_code_set_means_no_gate(clean_state) -> None:
    api = TestClient(app)
    assert api.get("/api/v1/demo/access").json()["required"] is False
    assert api.get("/api/v1/receiving/samples").status_code == 200


def test_api_needs_the_code_but_health_and_the_gate_do_not(clean_state) -> None:
    _lock(clean_state)
    api = TestClient(app)
    locked = api.get("/api/v1/receiving/samples")
    assert locked.status_code == 401
    assert locked.headers[access.LOCKED_HEADER] == "required"
    assert api.get("/health").status_code == 200
    assert api.get("/api/v1/demo/access").json() == {"required": True, "granted": False, "muse_calls_left": None}


def test_the_right_code_sets_a_cookie_that_opens_the_api(clean_state) -> None:
    code = _lock(clean_state)
    api = TestClient(app)
    assert api.post("/api/v1/demo/access", json={"code": "nope"}).status_code == 401
    entered = api.post("/api/v1/demo/access", json={"code": f"  {code} "})
    assert entered.status_code == 200 and entered.json()["granted"] is True
    assert "httponly" in entered.headers["set-cookie"].lower()
    assert api.get("/api/v1/receiving/samples").status_code == 200
    assert api.get("/api/v1/demo/access").json()["granted"] is True


def test_changing_the_code_signs_everyone_out(clean_state) -> None:
    code = _lock(clean_state)
    api = TestClient(app)
    api.post("/api/v1/demo/access", json={"code": code})
    _lock(clean_state, "new-code")
    assert api.get("/api/v1/receiving/samples").status_code == 401


def test_tokens_cannot_be_forged_or_used_after_they_expire() -> None:
    token = access.make_token("secret", now=1_000)
    assert access.token_is_valid(token, "secret", now=2_000)
    assert not access.token_is_valid(token, "other", now=2_000)
    assert not access.token_is_valid(token, "secret", now=1_000 + access.MAX_AGE_SECONDS + 1)
    version, expires, sig = token.split(".")
    assert not access.token_is_valid(f"{version}.{int(expires) + 999999}.{sig}", "secret", now=2_000)
    assert not access.token_is_valid("garbage", "secret")


def test_too_many_wrong_codes_pause_that_address(clean_state) -> None:
    code = _lock(clean_state)
    api = TestClient(app)
    for _ in range(access.MAX_FAILURES):
        assert api.post("/api/v1/demo/access", json={"code": "guess"}).status_code == 401
    paused = api.post("/api/v1/demo/access", json={"code": code})
    assert paused.status_code == 429 and "15 minutes" in paused.json()["detail"]


def test_the_failure_window_slides() -> None:
    limiter = access.FailureLimiter(limit=2, window=60)
    limiter.failed("a", now=0)
    limiter.failed("a", now=10)
    assert limiter.blocked("a", now=20) and not limiter.blocked("b", now=20)
    assert not limiter.blocked("a", now=65)


def test_public_mode_needs_a_code(monkeypatch) -> None:
    import importlib

    import backend.main as main_module

    settings = get_settings()
    monkeypatch.setattr(settings, "public_demo", True)
    monkeypatch.setattr(settings, "demo_access_code", None)
    with pytest.raises(RuntimeError, match="DEMO_ACCESS_CODE"):
        importlib.reload(main_module)
    monkeypatch.setattr(settings, "demo_access_code", SecretStr("x"))
    public = importlib.reload(main_module).app
    assert TestClient(public).get("/docs").status_code == 404
    monkeypatch.setattr(settings, "public_demo", False)
    monkeypatch.setattr(settings, "demo_access_code", None)
    importlib.reload(main_module)


# --- daily Muse limit --------------------------------------------------------------------------


class _Client:
    def __init__(self) -> None:
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))])


def test_each_model_call_counts_and_stops_at_the_limit(clean_state) -> None:
    clean_state.muse_daily_limit = 2
    client = _Client()
    call_model(client, model="m", messages=[])
    call_model(client, model="m", messages=[])
    with pytest.raises(muse_limit.MuseLimitReached):
        call_model(client, model="m", messages=[])
    assert client.calls == 2
    assert muse_limit.usage()["left"] == 0


def test_the_allowance_resets_at_midnight_pacific(clean_state) -> None:
    clean_state.muse_daily_limit = 1
    late = datetime.fromisoformat("2026-11-11T23:59:00-08:00")
    muse_limit.take(late)
    with pytest.raises(muse_limit.MuseLimitReached):
        muse_limit.check(late)
    muse_limit.check(datetime.fromisoformat("2026-11-12T00:00:30-08:00"))  # a new day in the café


def test_no_limit_by_default(clean_state) -> None:
    for _ in range(50):
        muse_limit.take()
    assert muse_limit.usage()["limit"] is None


def test_a_spent_allowance_is_a_friendly_429(clean_state) -> None:
    clean_state.muse_daily_limit = 0
    clean_state.model_api_key = SecretStr("test-key")
    response = TestClient(app).post("/api/v1/agent/chat", json={"message": "How much oat milk do we have?"})
    assert response.status_code == 429
    assert "today's Muse allowance" in response.json()["detail"]
    assert TestClient(app).get("/api/v1/demo/access").json()["muse_calls_left"] == 0


# --- sample photos and Start over --------------------------------------------------------------


@pytest.fixture
def session(tmp_path, monkeypatch) -> Session:
    monkeypatch.setattr(planner_module, "LLM_LOG_PATH", tmp_path / "llm.jsonl")
    monkeypatch.setattr(service, "IMAGE_DIR", tmp_path / "receiving")
    connection = engine.connect()
    transaction = connection.begin()
    db = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        if db.get(Cafe, C) is None:
            pytest.skip("demo data not seeded; run scripts/seed_all.sh")
        apply_reset(db, plan_reset(db))
        seed_event_surge(db, with_delivery=True)
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


class _Vision:
    def __init__(self, *replies: dict) -> None:
        self.replies = [json.dumps(r) for r in replies]
        self.images: list[str] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.images.append(kwargs["messages"][0]["content"][1]["image_url"]["url"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.replies.pop(0)))], usage=None)


def test_every_sample_file_exists_and_is_served() -> None:
    api = TestClient(app)
    listed = api.get("/api/v1/receiving/samples").json()
    assert [s["sample_id"] for s in listed] == [s.sample_id for s in SAMPLES]
    assert {s["kind"] for s in listed} == {"slip", "photo"}
    for sample in listed:
        image = api.get(sample["image_url"])
        assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg" and len(image.content) > 10_000
    assert api.get("/api/v1/receiving/samples/nope/image").status_code == 404


def test_a_picked_sample_is_what_muse_reads(session: Session) -> None:
    vision = _Vision(LIVE["slip"]["parsed"])
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_planner_client] = lambda: vision
    api = TestClient(app)
    rid = api.post("/api/v1/receiving/receipts", json={"cafe_id": C}).json()["receipt_id"]
    assert api.post(f"/api/v1/receiving/receipts/{rid}/slip", json={"sample_id": "slip-po-123"}).status_code == 200
    sent = base64.b64decode(vision.images[0].split(",", 1)[1])
    assert sent == SAMPLES[0].path.read_bytes()


def test_a_sample_of_the_wrong_kind_or_an_unknown_one_is_refused(session: Session) -> None:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_planner_client] = lambda: _Vision()
    api = TestClient(app)
    rid = api.post("/api/v1/receiving/receipts", json={"cafe_id": C}).json()["receipt_id"]
    wrong = api.post(f"/api/v1/receiving/receipts/{rid}/slip", json={"sample_id": "photo-po-123"})
    assert wrong.status_code == 422 and "packing slip" in wrong.json()["detail"]
    assert api.post(f"/api/v1/receiving/receipts/{rid}/photo", json={"sample_id": "nope"}).status_code == 422


def test_an_upload_over_8_mb_is_refused(session: Session) -> None:
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_planner_client] = lambda: _Vision()
    api = TestClient(app)
    rid = api.post("/api/v1/receiving/receipts", json={"cafe_id": C}).json()["receipt_id"]
    big = base64.b64encode(b"\xff" * (8 * 1024 * 1024 + 1)).decode()
    assert api.post(f"/api/v1/receiving/receipts/{rid}/slip", json={"image_base64": big}).status_code in (413, 422)


def test_start_over_removes_what_the_visitor_did(session: Session) -> None:
    app.dependency_overrides[get_db] = lambda: session
    api = TestClient(app)
    rid = api.post("/api/v1/receiving/receipts", json={"cafe_id": C}).json()["receipt_id"]
    assert session.get(Receipt, rid) is not None
    response = api.post("/api/v1/demo/reset")
    assert response.status_code == 200 and response.json()["reset"] is True
    session.expire_all()
    assert session.scalar(select(Receipt).where(Receipt.receipt_id == rid)) is None
