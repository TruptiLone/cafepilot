from fastapi import FastAPI

from backend.modules.inventory.router import router as inventory_router
from backend.modules.sales.router import router as sales_router

app = FastAPI(
    title="Cafe Business Core",
    description="Operational management system for CafePilot",
    version="0.1.0",
)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "application": "cafe-business-core"}


app.include_router(inventory_router, prefix="/api/v1/inventory")
app.include_router(sales_router, prefix="/api/v1/sales")
