
from fastapi import FastAPI
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, Field
from backend.agents.cafe_agent import chat_with_agent
from backend.services.catering_service import evaluate_catering_order

# Create our CafePilot application
app = FastAPI(title="CafePilot API")

# Temporary inventory data.
# Later, this will come from PostgreSQL.
inventory = [
    {
        "id": 1,
        "name": "Oat Milk",
        "quantity": 8,
        "unit": "liters",
        "minimum_stock": 15
    },
    {
        "id": 2,
        "name": "Whole Milk",
        "quantity": 24,
        "unit": "liters",
        "minimum_stock": 20
    },
    {
        "id": 3,
        "name": "Coffee Beans",
        "quantity": 5,
        "unit": "kg",
        "minimum_stock": 4
    }
]


# Home endpoint
@app.get("/")
def home():
    return {
        "application": "CafePilot",
        "status": "running",
        "message": "Welcome to CafePilot!"
    }


# Return inventory with low-stock status
@app.get("/inventory")
def get_inventory():
    results = []

    for item in inventory:
        stock_status = (
            "LOW"
            if item["quantity"] < item["minimum_stock"]
            else "OK"
        )

        results.append({
            **item,
            "status": stock_status
        })

    return {"inventory": results}


# Defines a single product in a catering request.
class CateringItem(BaseModel):
    product_id: str
    size: Literal["regular", "large", "each"] = "regular"
    quantity: int = Field(gt=0)
    milk: Literal["whole", "skim", "oat", "almond"] = "whole"
    extra_shots: int = Field(default=0, ge=0)


# Defines the full catering request.
class CateringRequest(BaseModel):
    items: list[CateringItem] = Field(min_length=1)


@app.post("/catering/readiness")
def catering_readiness(request: CateringRequest):
    """
    Check ingredient readiness for a catering order.
    This endpoint does not modify inventory.
    """
    try:
        items = [
            item.model_dump()
            for item in request.items
        ]

        report = evaluate_catering_order(items)

        return report

    except ValueError as error:
        # Unknown product, invalid size, or incompatible modifier.
        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1)


@app.post("/agent/chat")
def agent_chat(request: AgentChatRequest):
    """Send an owner question to our CafePilot agent."""
    try:
        return chat_with_agent(request.message)

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        ) from error


