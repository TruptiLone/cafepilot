import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine
from backend.modules.agent.registry import REGISTRY, describe_for_planner
from backend.modules.agent.superset import build_superset, get_superset
from scripts.seed_sales_catalog import catalog_identifier
from tests.sales_v2_support import seed_sales_v2_test_catalog

TEST_CAFE_ID = "TEST_AGENT_REGISTRY_CAFE"


@pytest.fixture
def database_session() -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")()
    try:
        seed_sales_v2_test_catalog(session, TEST_CAFE_ID)
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _id(local_id: str) -> str:
    return catalog_identifier(TEST_CAFE_ID, local_id)


# ---------- registry shape -------------------------------------------------


def test_agent_cannot_create_sales_or_employees() -> None:
    assert "create_sale" not in REGISTRY
    assert "create_employee" not in REGISTRY


def test_every_write_requires_owner_approval_and_reads_do_not() -> None:
    for action in REGISTRY.values():
        if action.side_effect == "write":
            assert action.approval == "owner", action.name
        else:
            assert action.approval == "none", action.name


def test_every_action_has_generated_schemas() -> None:
    for action in REGISTRY.values():
        assert action.input_schema()["type"] == "object", action.name
        assert action.output_schema(), action.name


def test_planner_description_is_json_and_covers_every_action() -> None:
    description = describe_for_planner()
    json.dumps(description)
    assert [entry["name"] for entry in description] == list(REGISTRY)


# ---------- superset DAG ---------------------------------------------------


def test_superset_is_acyclic_and_orders_every_action() -> None:
    order = get_superset().topological_order()
    assert sorted(order) == sorted(REGISTRY)


@pytest.mark.parametrize(
    ("upstream", "downstream"),
    [
        ("coverage_report", "schedule_shift"),
        ("list_employees", "schedule_shift"),
        ("list_shifts", "schedule_shift"),
        ("list_shifts", "cancel_shift"),
        ("get_stock_estimates", "record_inventory_movement"),
        ("calculate_shortfall", "propose_supplier_order"),
        ("create_stock_count", "verify_stock_count"),
        ("get_stock_count", "verify_stock_count"),
    ],
)
def test_safety_edges(upstream: str, downstream: str) -> None:
    assert (upstream, downstream) in get_superset().safety_edges


@pytest.mark.parametrize(
    ("upstream", "downstream"),
    [
        ("list_products", "project_recipe_usage"),
        ("project_recipe_usage", "propose_supplier_order"),
        ("calculate_shortfall", "propose_supplier_order"),
        ("propose_supplier_order", "record_inventory_movement"),
        ("list_ingredients", "get_stock_estimates"),
    ],
)
def test_data_edges_are_derived_from_produces_and_consumes(upstream: str, downstream: str) -> None:
    assert (upstream, downstream) in get_superset().data_edges


def test_read_actions_have_no_mandatory_prerequisites() -> None:
    for action in REGISTRY.values():
        if action.side_effect == "read":
            assert action.requires == (), action.name


def test_unknown_requirement_is_rejected() -> None:
    from dataclasses import replace

    broken = dict(REGISTRY)
    broken["cancel_shift"] = replace(REGISTRY["cancel_shift"], requires=(("no_such_action",),))
    with pytest.raises(RuntimeError, match="unknown action"):
        build_superset(broken)


def test_cycle_is_rejected() -> None:
    from dataclasses import replace

    looped = dict(REGISTRY)
    looped["list_employees"] = replace(REGISTRY["list_employees"], requires=(("schedule_shift",),))
    with pytest.raises(RuntimeError, match="cycle"):
        build_superset(looped)


# ---------- running actions against the domain services --------------------


def test_read_actions_return_json_safe_results(database_session: Session) -> None:
    estimates = REGISTRY["get_stock_estimates"].run(database_session, {"cafe_id": TEST_CAFE_ID})
    json.dumps(estimates)
    assert estimates and all(row["stock_status"] == "unknown" for row in estimates)
    assert all(row["estimated_quantity"] is None for row in estimates)

    projection = REGISTRY["project_recipe_usage"].run(
        database_session,
        {
            "cafe_id": TEST_CAFE_ID,
            "target_at": "2026-11-01T09:00:00-08:00",
            "items": [{"variation_id": _id("COF003_REG"), "quantity": 2}],
        },
    )
    quantities = {row["ingredient_id"]: row["quantity"] for row in projection["ingredients"]}
    assert quantities[_id("WHOLE_MILK")] == "480.000"


def test_invalid_args_are_rejected_before_any_service_call(database_session: Session) -> None:
    with pytest.raises(ValidationError):
        REGISTRY["get_stock_estimates"].run(
            database_session, {"cafe_id": TEST_CAFE_ID, "as_of": "2026-10-29T18:00:00"}
        )
    with pytest.raises(ValidationError):
        REGISTRY["list_employees"].run(database_session, {"cafe_id": TEST_CAFE_ID, "bogus": 1})


def test_simulated_supplier_order_is_labeled_and_deterministic(database_session: Session) -> None:
    args = {
        "cafe_id": TEST_CAFE_ID,
        "lines": [{"ingredient_id": _id("OAT_MILK"), "quantity": "2000", "unit": "mL"}],
        "requested_delivery_at": "2026-11-01T06:00:00-08:00",
    }
    first = REGISTRY["propose_supplier_order"].run(database_session, args)
    second = REGISTRY["propose_supplier_order"].run(database_session, args)
    assert first["simulated"] is True
    assert first["status"] == "confirmed"
    assert first["order_ref"] == second["order_ref"]


def test_simulated_supplier_order_rejects_wrong_unit(database_session: Session) -> None:
    with pytest.raises(ValueError, match="must be ordered in mL"):
        REGISTRY["propose_supplier_order"].run(
            database_session,
            {
                "cafe_id": TEST_CAFE_ID,
                "lines": [{"ingredient_id": _id("OAT_MILK"), "quantity": "2", "unit": "case"}],
                "requested_delivery_at": datetime(2026, 11, 1, 14, tzinfo=timezone.utc).isoformat(),
            },
        )
