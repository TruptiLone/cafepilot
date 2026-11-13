"""A private café per visitor: two visitors never see each other's data (real Postgres copies).

Builds a small template (migrations + the inventory seed) once for the module, then makes visitor copies
from it the way the public demo does. Skipped when the database user can't create databases.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.db import visitors as visitor_module
from backend.db.config import get_settings
from backend.db.session import engine
from backend.db.visitors import VisitorDatabases, VisitorError, database_name
from backend.main import app
from backend.modules.demo import access

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = "test_cafepilot_template"
C = "CAFE001"


def _admin_exec(sql: str) -> None:
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text(sql))


def _exists(name: str) -> bool:
    with engine.connect() as conn:
        return conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).scalar() is not None


@pytest.fixture(scope="module")
def template():
    with engine.connect() as conn:
        can_create = conn.execute(text("SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user")).scalar()
    if not can_create:
        pytest.skip("the database user can't create databases")
    if _exists(TEMPLATE):
        _admin_exec(f'ALTER DATABASE "{TEMPLATE}" WITH IS_TEMPLATE false ALLOW_CONNECTIONS true')
        _admin_exec(f'DROP DATABASE "{TEMPLATE}" WITH (FORCE)')
    _admin_exec(f'CREATE DATABASE "{TEMPLATE}"')
    env = {**os.environ, "POSTGRES_DB": TEMPLATE, "PYTHONPATH": str(ROOT)}
    for args in (["-m", "alembic", "upgrade", "head"], ["scripts/seed_inventory.py"]):
        subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=True, capture_output=True)
    _admin_exec(f'ALTER DATABASE "{TEMPLATE}" WITH IS_TEMPLATE true ALLOW_CONNECTIONS false')
    yield TEMPLATE
    _admin_exec(f'ALTER DATABASE "{TEMPLATE}" WITH IS_TEMPLATE false ALLOW_CONNECTIONS true')
    _admin_exec(f'DROP DATABASE "{TEMPLATE}" WITH (FORCE)')


@pytest.fixture
def copies(template, monkeypatch):
    """Per-visitor mode on, with its own registry; every copy made in the test is dropped afterwards."""
    settings = get_settings()
    monkeypatch.setattr(settings, "per_visitor_databases", True)
    monkeypatch.setattr(settings, "template_database", template)
    monkeypatch.setattr(settings, "max_visitor_databases", 3)
    monkeypatch.setattr(settings, "demo_access_code", None)
    registry = VisitorDatabases()
    monkeypatch.setattr(visitor_module, "visitors", registry)
    made: set[str] = set()
    real_create = registry.create

    def create(visitor_id=None):
        visitor_id = real_create(visitor_id)
        made.add(database_name(visitor_id))
        return visitor_id

    monkeypatch.setattr(registry, "create", create)
    yield registry
    registry.dispose()
    for name in made:
        _admin_exec(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _visitor() -> TestClient:
    client = TestClient(app)
    assert client.get("/api/v1/demo/access").status_code == 200  # first visit: their café is made here
    return client


def _start_check(client: TestClient) -> str:
    response = client.post("/api/v1/receiving/receipts", json={"cafe_id": C})
    assert response.status_code == 200, response.text
    return response.json()["receipt_id"]


def test_two_visitors_never_see_each_others_data(copies) -> None:
    ana, ben = _visitor(), _visitor()
    a_id, b_id = ana.cookies[access.VISITOR_COOKIE], ben.cookies[access.VISITOR_COOKIE]
    assert a_id != b_id and copies.count() == 2

    receipt = _start_check(ana)
    assert ana.get(f"/api/v1/receiving/receipts/{receipt}").status_code == 200
    assert ben.get(f"/api/v1/receiving/receipts/{receipt}").status_code == 404

    # Stock is per café too: both start from the same template.
    assert ana.get("/api/v1/inventory/items").json() == ben.get("/api/v1/inventory/items").json()
    # Nothing reached the main database.
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1 FROM receipts WHERE receipt_id = :r"), {"r": receipt}).scalar() is None


def test_a_visit_keeps_its_cafe(copies) -> None:
    ana = _visitor()
    first = ana.cookies[access.VISITOR_COOKIE]
    ana.get("/api/v1/demo/access")
    assert ana.cookies[access.VISITOR_COOKIE] == first and copies.count() == 1


def test_no_cafe_means_the_page_asks_the_gate(copies) -> None:
    stranger = TestClient(app)
    locked = stranger.get("/api/v1/inventory/items")
    assert locked.status_code == 401 and locked.headers[access.LOCKED_HEADER] == "required"
    stranger.cookies.set(access.VISITOR_COOKIE, "0123456789abcdef")  # an id with no café behind it
    assert stranger.get("/api/v1/inventory/items").status_code == 401
    stranger.cookies.set(access.VISITOR_COOKIE, "x'; DROP DATABASE postgres; --")
    assert stranger.get("/api/v1/inventory/items").status_code == 401


def test_a_closed_cafe_is_made_fresh_again_with_the_same_cookie(copies) -> None:
    ana = _visitor()
    visitor_id = ana.cookies[access.VISITOR_COOKIE]
    receipt = _start_check(ana)
    copies.drop(database_name(visitor_id))
    assert ana.get("/api/v1/inventory/items").status_code == 401
    ana.get("/api/v1/demo/access")
    assert ana.cookies[access.VISITOR_COOKIE] == visitor_id
    assert ana.get(f"/api/v1/receiving/receipts/{receipt}").status_code == 404  # a fresh café


def test_start_over_replaces_only_your_own_cafe(copies) -> None:
    ana, ben = _visitor(), _visitor()
    mine, theirs = _start_check(ana), _start_check(ben)
    reset = ana.post("/api/v1/demo/reset")
    assert reset.status_code == 200 and reset.json()["reset"] is True
    assert ana.get(f"/api/v1/receiving/receipts/{mine}").status_code == 404
    assert ben.get(f"/api/v1/receiving/receipts/{theirs}").status_code == 200


def test_idle_cafes_are_dropped(copies) -> None:
    ana = _visitor()
    name = database_name(ana.cookies[access.VISITOR_COOKIE])
    assert copies.reap() == []
    later = time.monotonic() + get_settings().visitor_idle_minutes * 60 + 1
    assert copies.reap(now=later) == [name]
    assert not _exists(name) and copies.count() == 0


def test_a_full_demo_says_so_and_frees_the_longest_idle_cafe(copies, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "max_visitor_databases", 2)
    first, _ = _visitor(), _visitor()
    full = TestClient(app).get("/api/v1/demo/access")
    assert full.status_code == 503 and "busy" in full.json()["detail"]

    oldest = database_name(first.cookies[access.VISITOR_COOKIE])
    copies._last_seen[oldest] -= visitor_module.EVICT_AFTER_SECONDS + 1  # idle a while
    assert TestClient(app).get("/api/v1/demo/access").status_code == 200
    assert not _exists(oldest)


def test_no_template_is_a_clear_message(copies, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "template_database", "no_such_template")
    response = TestClient(app).get("/api/v1/demo/access")
    assert response.status_code == 503 and "template" in response.json()["detail"]
    assert copies.count() == 0


def test_copies_from_before_a_restart_are_adopted(copies) -> None:
    ana = _visitor()
    name = database_name(ana.cookies[access.VISITOR_COOKIE])
    fresh = VisitorDatabases()
    assert name in fresh.adopt_existing()
    fresh.dispose()


def test_unsafe_names_are_refused() -> None:
    with pytest.raises(ValueError):
        database_name("../etc")
    with pytest.raises(ValueError):
        visitor_module.check_identifier('x"; DROP')
    with pytest.raises(VisitorError):
        raise VisitorError("x")
