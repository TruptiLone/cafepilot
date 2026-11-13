from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.config import get_database_connect_args, get_settings

engine = create_engine(
    get_settings().database_url,
    connect_args=get_database_connect_args(),
    pool_pre_ping=True,
)


class RoutingSession(Session):
    """Binds to the visitor's own copy when the request has one (backend/db/visitors.py), else the main database."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        from backend.db.visitors import current_database, visitors

        name = current_database.get()
        if name is not None:
            kwargs["bind"] = visitors.engine(name)
        super().__init__(*args, **kwargs)


SessionLocal = sessionmaker(bind=engine, class_=RoutingSession, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    database_session = SessionLocal()
    try:
        yield database_session
    finally:
        database_session.close()
