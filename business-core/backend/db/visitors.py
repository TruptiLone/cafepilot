"""A private copy of the demo café for each visitor (PER_VISITOR_DATABASES=1).

Each visitor gets ``cafe_v_<id>``, made with ``CREATE DATABASE … TEMPLATE cafepilot_template`` (see
scripts/build_template.py); ``<id>`` lives in their cookie. The request's database is kept in a context
variable, and every session made from ``SessionLocal`` (``get_db``, ``ai_calls``) binds to it, so feature
code doesn't change. Copies idle for VISITOR_IDLE_MINUTES are dropped; at most MAX_VISITOR_DATABASES exist.

State is in memory (one server process); on start-up existing copies are adopted as just seen.
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from contextvars import ContextVar

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from backend.db.config import get_database_connect_args, get_settings

logger = logging.getLogger("cafepilot.visitors")

PREFIX = "cafe_v_"
_ID = re.compile(r"^[0-9a-f]{16}$")
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
# A copy this idle may be closed early when every slot is taken.
EVICT_AFTER_SECONDS = 10 * 60

current_database: ContextVar[str | None] = ContextVar("current_database", default=None)


class VisitorError(RuntimeError):
    """A visitor copy can't be made right now; the message is for the visitor."""


def check_identifier(name: str) -> str:
    if not _IDENTIFIER.match(name):
        raise ValueError(f"not a safe database name: {name!r}")
    return name


def valid_id(value: str | None) -> bool:
    return bool(value and _ID.match(value))


def database_name(visitor_id: str) -> str:
    if not valid_id(visitor_id):
        raise ValueError("bad visitor id")
    return f"{PREFIX}{visitor_id}"


def enabled() -> bool:
    return get_settings().per_visitor_databases


class VisitorDatabases:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_seen: dict[str, float] = {}  # database name -> monotonic time
        self._engines: dict[str, Engine] = {}
        self._admin: Engine | None = None

    # --- connections ---------------------------------------------------------------------------

    def admin(self) -> Engine:
        """Autocommit connection to the main database, for CREATE / DROP DATABASE."""
        with self._lock:
            if self._admin is None:
                self._admin = create_engine(get_settings().database_url, connect_args=get_database_connect_args(),
                                            isolation_level="AUTOCOMMIT", pool_size=1, max_overflow=1,
                                            pool_pre_ping=True)
            return self._admin

    def engine(self, name: str) -> Engine:
        """A small pool per copy: one visitor rarely needs more than two connections at once."""
        with self._lock:
            engine = self._engines.get(name)
            if engine is None:
                url = get_settings().database_url.set(database=check_identifier(name))
                engine = create_engine(url, connect_args=get_database_connect_args(), pool_size=2, max_overflow=2,
                                       pool_pre_ping=True, pool_recycle=1800)
                self._engines[name] = engine
            return engine

    # --- life cycle ----------------------------------------------------------------------------

    def adopt_existing(self) -> list[str]:
        """On start-up: copies left from before a restart count as just seen."""
        with self.admin().connect() as conn:
            names = conn.execute(text("SELECT datname FROM pg_database WHERE datname LIKE :p"),
                                 {"p": PREFIX.replace("_", r"\_") + "%"}).scalars().all()
        now = time.monotonic()
        with self._lock:
            for name in names:
                self._last_seen.setdefault(name, now)
        return list(names)

    def has(self, visitor_id: str | None) -> bool:
        return valid_id(visitor_id) and database_name(visitor_id) in self._last_seen  # type: ignore[arg-type]

    def touch(self, visitor_id: str) -> None:
        with self._lock:
            name = database_name(visitor_id)
            if name in self._last_seen:
                self._last_seen[name] = time.monotonic()

    def count(self) -> int:
        return len(self._last_seen)

    def create(self, visitor_id: str | None = None) -> str:
        """Make a fresh copy from the template; returns the visitor id."""
        settings = get_settings()
        template = check_identifier(settings.template_database)
        with self._lock:
            visitor_id = visitor_id if valid_id(visitor_id) else secrets.token_hex(8)
            name = database_name(visitor_id)
            if name not in self._last_seen and len(self._last_seen) >= settings.max_visitor_databases:
                self._evict_one()
            self._last_seen[name] = time.monotonic()  # holds the slot while the copy is made
        try:
            started = time.monotonic()
            with self.admin().connect() as conn:
                conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{template}"'))
            logger.info("made %s in %.1f s", name, time.monotonic() - started)
        except SQLAlchemyError as error:
            with self._lock:
                self._last_seen.pop(name, None)
            message = str(getattr(error, "orig", error))
            if "does not exist" in message and template in message:
                raise VisitorError("The demo isn't ready yet: its template café hasn't been built.") from error
            raise
        return visitor_id

    def drop(self, name: str) -> None:
        with self._lock:
            self._last_seen.pop(name, None)
            engine = self._engines.pop(name, None)
        if engine is not None:
            engine.dispose()
        with self.admin().connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{check_identifier(name)}" WITH (FORCE)'))
        logger.info("dropped %s", name)

    def recreate(self, visitor_id: str) -> None:
        """Start over: the visitor's copy is replaced with a fresh one (same id, same cookie)."""
        name = database_name(visitor_id)
        self.drop(name)
        self.create(visitor_id)

    def _evict_one(self) -> None:
        # Called with the lock held: close the copy idle the longest, if it has been idle a while.
        oldest = min(self._last_seen.items(), key=lambda item: item[1], default=None)
        if oldest is None or time.monotonic() - oldest[1] < EVICT_AFTER_SECONDS:
            raise VisitorError("The demo is busy right now: every demo café is in use. Try again in a few minutes.")
        self.drop(oldest[0])

    def reap(self, now: float | None = None) -> list[str]:
        """Drop copies idle longer than VISITOR_IDLE_MINUTES."""
        limit = get_settings().visitor_idle_minutes * 60
        now = now if now is not None else time.monotonic()
        with self._lock:
            idle = [name for name, seen in self._last_seen.items() if now - seen > limit]
        for name in idle:
            try:
                self.drop(name)
            except SQLAlchemyError:
                logger.exception("couldn't drop %s", name)
        return idle

    def dispose(self) -> None:
        with self._lock:
            engines = list(self._engines.values()) + ([self._admin] if self._admin else [])
            self._engines.clear()
            self._admin = None
        for engine in engines:
            engine.dispose()


visitors = VisitorDatabases()


class Reaper:
    """A background thread that drops idle copies once a minute."""

    def __init__(self, every_seconds: float = 60) -> None:
        self.every = every_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="visitor-reaper", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.every):
            try:
                visitors.reap()
            except Exception:  # pragma: no cover - keep reaping
                logger.exception("reaper failed")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
