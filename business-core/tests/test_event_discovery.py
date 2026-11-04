"""Events tab data: upcoming vs past by the demo clock, and the demo's scripted market discovery."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from backend.modules.agent.events import hide_demo_discovery
from backend.modules.agent.router import get_planner_client
from scripts.seed_event_surge import apply_reset, plan_reset, seed_event_surge

C = "CAFE001"
MARKET = "EVT_SC_FARMERS_MARKET_20261108"
DEMO_NOW = "2026-11-05T18:00:00-08:00"


@pytest.fixture
def db_and_api():
    connection = engine.connect()
    transaction = connection.begin()
    db: Session = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        if db.get(Cafe, C) is None:
            pytest.skip("demo data not seeded; run scripts/seed_all.sh")
        apply_reset(db, plan_reset(db))
        seed_event_surge(db)
        hide_demo_discovery(db, C)  # what seed_event_surge.py --fresh does
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_planner_client] = lambda: object()  # no model needed (CI has no key)
        yield db, TestClient(app)
    finally:
        app.dependency_overrides.clear()
        db.close()
        transaction.rollback()
        connection.close()


def _ids(api: TestClient) -> list[str]:
    return [e["event_id"] for e in api.get("/api/v1/agent/events", params={"cafe_id": C}).json()]


def test_after_a_fresh_reset_the_market_is_hidden_until_muse_finds_it(db_and_api) -> None:
    _, api = db_and_api
    assert MARKET not in _ids(api)
    assert _ids(api) == ["EVT_THANKSGIVING_20261126", "EVT_HOLIDAY_FAIR_20261205"]
    assert api.get(f"/api/v1/agent/events/{MARKET}").status_code == 404
    assert api.post(f"/api/v1/agent/events/{MARKET}/runs").status_code == 404  # can't be planned before it's found

    found = api.post("/api/v1/agent/events/discover-demo", params={"cafe_id": C}).json()
    assert [(e["event_id"], e["title"], e["plan_state"]) for e in found] == [(MARKET, "Santa Clara Farmers Market", "new")]
    assert _ids(api)[0] == MARKET  # stays visible (a reload keeps it)
    assert api.post("/api/v1/agent/events/discover-demo", params={"cafe_id": C}).json() == []  # once per reset


def test_the_discovery_shows_in_updates_as_simulated(db_and_api) -> None:
    _, api = db_and_api
    assert not [u for u in api.get("/api/v1/agent/updates", params={"cafe_id": C}).json() if u["kind"] == "event_found"]
    api.post("/api/v1/agent/events/discover-demo", params={"cafe_id": C})
    found = [u for u in api.get("/api/v1/agent/updates", params={"cafe_id": C}).json() if u["kind"] == "event_found"]
    assert [(u["title"], u["simulated"]) for u in found] == [("Muse found a new event: Santa Clara Farmers Market, Sun Nov 8", True)]


def test_past_events_are_the_latest_before_the_demo_clock(db_and_api) -> None:
    _, api = db_and_api
    past = api.get("/api/v1/agent/events/past", params={"cafe_id": C, "as_of": DEMO_NOW, "limit": 3}).json()
    assert [(e["title"], e["starts_at"][:10]) for e in past][0] == ("Halloween", "2026-10-31")
    assert past[0]["note"] == "Trick-or-treat afternoon; usually quieter"
    assert len(past) == 3 and all(e["starts_at"] < "2026-11-05" for e in past)
    assert api.get("/api/v1/agent/events/past", params={"cafe_id": C, "as_of": "2026-11-05T18:00:00"}).status_code == 422


def test_the_reset_hides_the_market_again(db_and_api) -> None:
    db, api = db_and_api
    api.post("/api/v1/agent/events/discover-demo", params={"cafe_id": C})
    assert MARKET in _ids(api)
    assert hide_demo_discovery(db, C) == 1
    assert MARKET not in _ids(api)
