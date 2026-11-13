"""Build the clean demo café that every visitor's private copy starts from (the template database).

Run from business-core/ (the database user needs CREATEDB):
    PYTHONPATH=. python scripts/build_template.py              # builds cafepilot_template (TEMPLATE_DATABASE)

Steps:
  1. create <name>_build next to the main database
  2. migrations + every seed (scripts/seed_all.sh order) + the market story, fresh (seed_event_surge --fresh)
  3. swap it in: drop the old template, rename, mark it a template that nobody can connect to
Visitors' existing copies are not touched. Prints the template's size and how long one copy takes
(Nov 11 2026: 35 MB, 0.3 s per copy, so the sales history needs no trimming).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text

from backend.db.config import get_database_connect_args, get_settings
from backend.db.visitors import check_identifier

ROOT = Path(__file__).resolve().parents[1]
SEEDS = ["seed_inventory", "seed_sales", "seed_sales_catalog", "seed_sales_recipes", "seed_demo_operations",
         "seed_sales_history"]


def _admin():
    """Autocommit connection to the main database: CREATE / DROP DATABASE can't run in a transaction."""
    engine = create_engine(get_settings().database_url, connect_args=get_database_connect_args(),
                           isolation_level="AUTOCOMMIT")
    return engine


def wait_for_database(tries: int = 30, pause: float = 2.0) -> None:
    """In a container the database may still be starting: try for about a minute before giving up."""
    engine = _admin()
    try:
        for attempt in range(1, tries + 1):
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                return
            except Exception as error:  # noqa: BLE001 - any connection failure means "not yet"
                if attempt == tries:
                    raise SystemExit(f"The database isn't reachable: {str(error).splitlines()[0]}") from error
                print(f"Waiting for the database ({attempt}/{tries})…", flush=True)
                time.sleep(pause)
    finally:
        engine.dispose()


def _exists(conn, name: str) -> bool:
    return conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).scalar() is not None


def _drop(conn, name: str) -> None:
    if _exists(conn, name):
        conn.execute(text(f'ALTER DATABASE "{name}" WITH IS_TEMPLATE false ALLOW_CONNECTIONS true'))
        conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


def _run(args: list[str], database: str) -> None:
    env = {**os.environ, "POSTGRES_DB": database, "PYTHONPATH": str(ROOT)}
    print("==>", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, env=env, check=True)


def build(name: str) -> None:
    check_identifier(name)
    building = f"{name}_build"
    admin = _admin()
    with admin.connect() as conn:
        _drop(conn, building)
        conn.execute(text(f'CREATE DATABASE "{building}"'))
    python = sys.executable
    _run([python, "-m", "alembic", "upgrade", "head"], building)
    for seed in SEEDS:
        _run([python, f"scripts/{seed}.py"], building)
    _run([python, "scripts/seed_event_surge.py", "--fresh"], building)

    with admin.connect() as conn:
        _drop(conn, name)
        conn.execute(text(f'ALTER DATABASE "{building}" RENAME TO "{name}"'))
        conn.execute(text(f'ALTER DATABASE "{name}" WITH IS_TEMPLATE true ALLOW_CONNECTIONS false'))
        size = conn.execute(text("SELECT pg_size_pretty(pg_database_size(:n))"), {"n": name}).scalar()
        started = time.monotonic()
        conn.execute(text(f'CREATE DATABASE "{name}_timing" TEMPLATE "{name}"'))
        seconds = time.monotonic() - started
        conn.execute(text(f'DROP DATABASE "{name}_timing" WITH (FORCE)'))
    admin.dispose()
    print(f"Template {name} ready: {size}; one visitor copy takes {seconds:.1f} s.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default=get_settings().template_database)
    parser.add_argument("--wait", action="store_true", help="wait up to a minute for the database first")
    args = parser.parse_args()
    if args.wait:
        wait_for_database()
    build(args.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
