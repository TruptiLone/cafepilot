"""The 1-half-gallon glasses test order: test-only supplier, never offered to Muse or find_suppliers."""

import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from backend.db.session import engine
from backend.models.cafe import Cafe
from backend.modules.agent.executor import decide
from backend.modules.agent.planner import build_planning_context
from backend.modules.purchasing.models import PurchaseOrder
from backend.modules.purchasing.service import OrderLineIn, get_supplier, settle_due, supplier_options

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import glasses_test_order  # noqa: E402

C = "CAFE001"


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


def test_the_test_supplier_is_never_offered_for_planning(session: Session) -> None:
    lines = [OrderLineIn("WHOLE_MILK", Decimal("1890"), "mL")]
    options = supplier_options(session, C, lines, datetime.now(timezone.utc) + timedelta(days=1))
    assert "GLASSES_TEST" not in [o["supplier_id"] for o in options["suppliers"]]
    context = build_planning_context(session, C)
    assert "GLASSES_TEST" not in json.dumps(context.reference, default=str)
    assert get_supplier(C, "GLASSES_TEST")["items"]["WHOLE_MILK"]["pack_size"] == "1890"  # only when named


def test_the_test_order_is_one_half_gallon_and_waits_for_the_owner(session: Session, tmp_path) -> None:
    run = glasses_test_order.make_order(session)
    node = run.nodes[0]
    assert run.status == "awaiting_approval" and node.status == "awaiting_approval"
    assert any("waived" in w for w in run.warnings)

    decide(session, run.run_id, node.node_id, "approved", channel="api")
    order = session.query(PurchaseOrder).filter_by(source_run_id=run.run_id).one()
    settle_due(session, cafe_id=C, now=datetime.now(timezone.utc) + timedelta(minutes=5))
    session.refresh(order)
    line = order.lines[0]
    assert (order.status, order.supplier_id, line.packs, line.quantity, line.pack_name) == (
        "confirmed", "GLASSES_TEST", 1, Decimal("1890"), "half gallon (1.89 L)")

    png = glasses_test_order.draw_slip(session, order.po_number, tmp_path)
    assert png.exists() and png.with_suffix(".pdf").exists()
