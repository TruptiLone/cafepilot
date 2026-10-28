from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from backend.db import session
from backend.db.config import get_database_connect_args, get_settings
from backend.db.session import SessionLocal


@pytest.mark.database
def test_session_factory_executes_a_query() -> None:
    with SessionLocal() as database_session:
        assert database_session.execute(text("SELECT 1")).scalar_one() == 1


@pytest.mark.database
def test_application_sessions_use_utc_and_round_trip_aware_timestamps() -> None:
    source_timestamp = datetime(
        2025,
        1,
        6,
        8,
        15,
        tzinfo=timezone(timedelta(hours=-8)),
    )

    with SessionLocal() as database_session:
        assert database_session.scalar(text("SHOW TIME ZONE")) == "UTC"
        round_tripped = database_session.scalar(
            text("SELECT CAST(:value AS TIMESTAMPTZ)"),
            {"value": source_timestamp},
        )

    assert round_tripped == source_timestamp
    assert round_tripped.utcoffset() == timedelta(0)


@pytest.mark.database
def test_null_pool_connections_use_utc_independent_of_connection_default() -> None:
    non_utc_engine = create_engine(
        get_settings().database_url,
        connect_args={"options": "-c timezone=America/Los_Angeles"},
        poolclass=NullPool,
    )
    utc_engine = create_engine(
        get_settings().database_url,
        connect_args=get_database_connect_args(),
        poolclass=NullPool,
    )

    try:
        with non_utc_engine.connect() as connection:
            assert connection.scalar(text("SHOW TIME ZONE")) == "America/Los_Angeles"
        with utc_engine.connect() as connection:
            assert connection.scalar(text("SHOW TIME ZONE")) == "UTC"
    finally:
        non_utc_engine.dispose()
        utc_engine.dispose()


def test_get_db_closes_the_session(monkeypatch) -> None:
    class FakeSession:
        closed = False

        def close(self) -> None:
            self.closed = True

    database_session = FakeSession()
    monkeypatch.setattr(session, "SessionLocal", lambda: database_session)

    dependency = session.get_db()
    database_session = next(dependency)

    assert database_session is not None

    dependency.close()
    assert database_session.closed
