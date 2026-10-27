from fastapi import FastAPI

from services.sales.router import router

app = FastAPI(
    title="CafePilot Sales Service",
    description="Sales management service for CafePilot",
    version="0.1.0",
)

app.include_router(router)
