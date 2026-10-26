
from fastapi import FastAPI

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
