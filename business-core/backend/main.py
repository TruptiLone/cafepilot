import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from backend.db.config import get_settings
from backend.db.visitors import Reaper, VisitorError, visitors
from backend.frontend_files import mount_frontend
from backend.modules.agent.router import router as agent_router
from backend.modules.demo.access import DemoGate, access_code
from backend.modules.demo.muse_limit import MuseLimitReached
from backend.modules.demo.router import router as demo_router
from backend.modules.inventory.router import router as inventory_router
from backend.modules.receiving.router import router as receiving_router
from backend.modules.sales.router import router as sales_router
from backend.modules.staffing.router import router as staffing_router

_public = get_settings().public_demo
if _public and access_code() is None:
    raise RuntimeError("PUBLIC_DEMO is on but DEMO_ACCESS_CODE is not set: refusing to start an open public demo.")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """With a café copy per visitor: adopt copies left from before a restart and drop idle ones."""
    reaper = None
    if get_settings().per_visitor_databases:
        found = visitors.adopt_existing()
        logging.getLogger("cafepilot.visitors").info("visitor copies at start-up: %d", len(found))
        reaper = Reaper()
        reaper.start()
    try:
        yield
    finally:
        if reaper is not None:
            reaper.stop()
            visitors.dispose()


app = FastAPI(
    lifespan=lifespan,
    title="Cafe Business Core",
    description="Operational management system for CafePilot",
    version="0.1.0",
    # The public demo hides the API explorer; locally it stays at /docs.
    docs_url=None if _public else "/docs",
    redoc_url=None if _public else "/redoc",
    openapi_url=None if _public else "/openapi.json",
)
app.add_middleware(DemoGate)


logger = logging.getLogger("cafepilot")


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
    """Turn unexpected database failures on agent endpoints into an actionable message.

    Other modules keep their existing behaviour: the error propagates unchanged.
    """
    if not request.url.path.startswith("/api/v1/agent"):
        raise error
    logger.exception("database error on %s %s", request.method, request.url.path)
    original = getattr(error, "orig", None) or error
    first_line = str(original).strip().splitlines()[0][:200] if str(original).strip() else type(error).__name__
    hint = ""
    if "does not exist" in first_line or "UndefinedTable" in type(original).__name__:
        hint = " The database is missing a table or column: run `alembic upgrade head` in business-core."
    elif "connection" in first_line.lower():
        hint = " Is the database running? Try `docker compose up -d` in business-core."
    return JSONResponse(status_code=500, content={"detail": f"Database error: {first_line}.{hint}"})


@app.exception_handler(MuseLimitReached)
async def muse_limit_reached(request: Request, error: MuseLimitReached) -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": str(error)})


@app.exception_handler(VisitorError)
async def visitor_error(request: Request, error: VisitorError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(error)})


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "application": "cafe-business-core"}


app.include_router(inventory_router, prefix="/api/v1/inventory")
app.include_router(sales_router, prefix="/api/v1/sales")
app.include_router(staffing_router, prefix="/api/v1/staffing")
app.include_router(agent_router, prefix="/api/v1/agent")
app.include_router(receiving_router, prefix="/api/v1/receiving")
app.include_router(demo_router, prefix="/api/v1/demo")

# Last, after every API route: the built web app and its page links (the container sets FRONTEND_DIST).
if get_settings().frontend_dist is not None:
    mount_frontend(app, get_settings().frontend_dist)
