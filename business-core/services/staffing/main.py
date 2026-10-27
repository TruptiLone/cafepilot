from fastapi import FastAPI

from services.staffing.router import router

app = FastAPI(
    title="CafePilot Staffing Service",
    description="Staffing management service for CafePilot",
    version="0.1.0",
)

app.include_router(router)
