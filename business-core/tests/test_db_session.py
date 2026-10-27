from sqlalchemy import text
import pytest

from backend.db import session
from backend.db.session import SessionLocal


@pytest.mark.database
def test_session_factory_executes_a_query() -> None:
    with SessionLocal() as database_session:
        assert database_session.execute(text("SELECT 1")).scalar_one() == 1


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
