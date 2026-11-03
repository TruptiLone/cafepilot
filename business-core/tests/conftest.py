import pytest
from sqlalchemy.orm import sessionmaker

from backend.db.session import engine
from backend.modules.agent import ai_calls


@pytest.fixture(autouse=True)
def ai_call_rows(monkeypatch):
    """ai_calls rows written during a test go to one connection that is rolled back afterwards.

    Returns a session factory on that connection, so a test can read what was recorded.
    """
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, join_transaction_mode="create_savepoint")
    monkeypatch.setattr(ai_calls, "SESSION_FACTORY", factory)
    try:
        yield factory
    finally:
        transaction.rollback()
        connection.close()
