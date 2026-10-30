"""The café status panel is computed from the same data and code as Muse's plans."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine, get_db
from backend.main import app
from backend.models.cafe import Cafe
from scripts.seed_event_surge import apply_reset, plan_reset, seed_event_surge

C = "CAFE001"


@pytest.fixture
def api():
    connection = engine.connect()
    transaction = connection.begin()
    db: Session = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        if db.get(Cafe, C) is None:
            pytest.skip("demo data not seeded; run scripts/seed_all.sh")
        apply_reset(db, plan_reset(db))
        seed_event_surge(db)
        app.dependency_overrides[get_db] = lambda: db
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        db.close()
        transaction.rollback()
        connection.close()


def test_panel_prepares_for_the_market_not_halloween(api: TestClient) -> None:
    body = api.get("/api/v1/agent/status", params={"cafe_id": C}).json()
    assert body["focus_event_name"] == "Santa Clara Farmers Market"
    assert body["date_label"] == "Thursday, October 29"

    milk = {row["ingredient_id"]: row for row in body["inventory"]}
    assert milk["WHOLE_MILK"]["status"] == "short" and milk["OAT_MILK"]["status"] == "short"
    assert body["kpis"]["stock"]["value"] == "2"
    assert (body["kpis"]["coverage"]["value"], body["kpis"]["coverage"]["of"]) == ("2", "/ 4")

    sunday = body["coverage"][-1]
    assert sunday["is_event_day"] and sunday["halves"][0] == {"label": "AM", "staffed": 2, "required": 4}
    assert [d["halves"][0]["staffed"] for d in body["coverage"][:2]] == [2, 2]

    events = {e["name"]: e for e in body["events"]}
    assert events["Halloween"]["plan_state"] == "new" and "fewer drinks" in events["Halloween"]["note"]


def test_events_list_has_no_plan_until_the_owner_asks(api: TestClient) -> None:
    events = api.get("/api/v1/agent/events", params={"cafe_id": C}).json()
    assert {e["plan_state"] for e in events} == {"new"} and all(e["run_id"] is None for e in events)
