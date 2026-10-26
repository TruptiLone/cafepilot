
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)


def test_catering_readiness_endpoint():
    request = {
        "items": [
            {
                "product_id": "COF003",
                "size": "regular",
                "quantity": 20,
                "milk": "whole"
            },
            {
                "product_id": "COF003",
                "size": "regular",
                "quantity": 10,
                "milk": "oat"
            },
            {
                "product_id": "COF002",
                "size": "regular",
                "quantity": 10
            }
        ]
    }

    response = client.post(
        "/catering/readiness",
        json=request
    )

    assert response.status_code == 200

    result = response.json()

    assert result["total_drinks_or_items"] == 40
    assert result["status"] == "shortage"

    inventory = {
        item["ingredient_id"]: item
        for item in result["inventory_report"]["items"]
    }

    assert inventory["OAT_MILK"]["shortage_quantity"] == 400


def test_reject_negative_quantity():
    request = {
        "items": [
            {
                "product_id": "COF003",
                "quantity": -5
            }
        ]
    }

    response = client.post(
        "/catering/readiness",
        json=request
    )

    assert response.status_code == 422


def test_reject_unknown_product():
    request = {
        "items": [
            {
                "product_id": "INVALID",
                "quantity": 5
            }
        ]
    }

    response = client.post(
        "/catering/readiness",
        json=request
    )

    assert response.status_code == 400
