from fastapi import FastAPI

from backend.modules.inventory.router import router as inventory_router

app = FastAPI(
    title="CafePilot Inventory Service",
    description="Inventory management service for CafePilot",
    version="0.1.0",
)

app.include_router(inventory_router)
