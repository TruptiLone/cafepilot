import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from backend.modules.agent.router import router as agent_router
from backend.modules.inventory.router import router as inventory_router
from backend.modules.receiving.router import router as receiving_router
from backend.modules.sales.router import router as sales_router
from backend.modules.staffing.router import router as staffing_router

app = FastAPI(
    title="Cafe Business Core",
    description="Operational management system for CafePilot",
    version="0.1.0",
)


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


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "application": "cafe-business-core"}


app.include_router(inventory_router, prefix="/api/v1/inventory")
app.include_router(sales_router, prefix="/api/v1/sales")
app.include_router(staffing_router, prefix="/api/v1/staffing")
app.include_router(agent_router, prefix="/api/v1/agent")
app.include_router(receiving_router, prefix="/api/v1/receiving")
